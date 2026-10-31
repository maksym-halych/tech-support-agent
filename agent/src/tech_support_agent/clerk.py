"""Clerk session token verification, matching the lessons API's rules."""

from collections.abc import Callable
from typing import Any, Literal

import jwt
from pydantic import BaseModel, ConfigDict, SecretStr

Role = Literal["student", "teacher"]

# Resolves the public key that signed a token, normally from Clerk's JWKS.
KeyResolver = Callable[[str], Any]


class InvalidTokenError(Exception):
    """The token is malformed, expired or not issued for this app."""


class KeysUnavailableError(Exception):
    """Clerk's signing keys could not be fetched."""


class VerifiedUser(BaseModel):
    """The user a verified session token describes, with the token itself."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    email: str
    role: Role
    token: SecretStr


def role_from_claims(claims: dict[str, Any]) -> Role:
    """
    Derive the caller's role from the ``role`` claim.

    Args:
        claims: Verified token claims.

    Returns:
        ``teacher`` only for the exact claim value ``teacher``; otherwise ``student``.
    """

    return "teacher" if claims.get("role") == "teacher" else "student"


def jwks_key_resolver(issuer: str) -> KeyResolver:
    """
    Build a resolver that fetches and caches Clerk's signing keys.

    Args:
        issuer: The Clerk frontend API URL.

    Returns:
        A function mapping a token to the key that signed it.
    """

    client = jwt.PyJWKClient(f"{issuer}/.well-known/jwks.json", timeout=5)

    def resolve(token: str) -> Any:
        return client.get_signing_key_from_jwt(token).key

    return resolve


class TokenVerifier:
    """Verifies Clerk session tokens."""

    def __init__(
        self,
        issuer: str,
        authorized_parties: list[str],
        key_resolver: KeyResolver,
        leeway_seconds: int = 5,
    ) -> None:
        """
        Configure the verifier.

        Args:
            issuer: Expected ``iss`` claim (the Clerk frontend API URL).
            authorized_parties: Allowed ``azp`` origins; empty skips the check.
            key_resolver: Returns the public key for a token.
            leeway_seconds: Clock skew tolerated for ``exp``, ``nbf`` and ``iat``.
        """

        self._issuer = issuer
        self._authorized_parties = authorized_parties
        self._key_resolver = key_resolver
        self._leeway = leeway_seconds

    def verify(self, token: str) -> VerifiedUser:
        """
        Verify a session token.

        Args:
            token: The raw JWT from the ``Authorization`` header.

        Returns:
            The user described by the token.

        Raises:
            InvalidTokenError: If the signature, issuer, lifetime or ``azp`` is invalid.
            KeysUnavailableError: If Clerk's keys cannot be fetched.
        """

        try:
            key = self._key_resolver(token)
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self._issuer,
                leeway=self._leeway,
                options={
                    "require": ["exp", "iat", "iss", "sub"],
                    "verify_aud": False,
                },
            )
        except jwt.PyJWKClientConnectionError as error:
            raise KeysUnavailableError(str(error)) from error
        except jwt.PyJWTError as error:
            raise InvalidTokenError(str(error)) from error
        azp = claims.get("azp")
        if self._authorized_parties and azp and azp not in self._authorized_parties:
            raise InvalidTokenError(f"Unexpected azp {azp!r}")
        email = claims.get("email") or ""
        return VerifiedUser(
            id=claims["sub"],
            name=claims.get("name") or email or "Unnamed user",
            email=email,
            role=role_from_claims(claims),
            token=SecretStr(token),
        )
