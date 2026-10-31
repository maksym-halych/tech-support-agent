"""Clerk session token verification."""

import logging
from collections.abc import Callable
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from lessons_api.models import Caller, Role

logger = logging.getLogger(__name__)

# Resolves the public key that signed a token, normally from Clerk's JWKS.
KeyResolver = Callable[[str], Any]

bearer = HTTPBearer(auto_error=False)


class InvalidTokenError(Exception):
    """The token is missing, malformed, expired or not issued for this app."""


class KeysUnavailableError(Exception):
    """Clerk's signing keys could not be fetched."""


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
    """Verifies Clerk session tokens and maps their claims to a Caller."""

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

    def verify(self, token: str) -> Caller:
        """
        Verify a session token.

        Args:
            token: The raw JWT from the ``Authorization`` header.

        Returns:
            The caller described by the token.

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
        return Caller(
            id=claims["sub"],
            name=claims.get("name") or email or "Unnamed user",
            email=email,
            role=role_from_claims(claims),
        )


def verified_caller(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Caller:
    """
    Authenticate the request with the app's TokenVerifier.

    Args:
        request: The incoming request; ``app.state.verifier`` does the check.
        credentials: The bearer token, if any.

    Returns:
        The verified caller.

    Raises:
        HTTPException: 401 for a missing or invalid token, 503 if keys are unavailable.
    """

    unauthorized = HTTPException(
        401, "Invalid or missing session token", {"WWW-Authenticate": "Bearer"}
    )
    if credentials is None:
        raise unauthorized
    verifier: TokenVerifier = request.app.state.verifier
    try:
        return verifier.verify(credentials.credentials)
    except InvalidTokenError as error:
        logger.info("Rejected session token: %s", error)
        raise unauthorized from None
    except KeysUnavailableError as error:
        logger.warning("Cannot fetch Clerk signing keys: %s", error)
        raise HTTPException(503, "Cannot verify the session token right now") from None
