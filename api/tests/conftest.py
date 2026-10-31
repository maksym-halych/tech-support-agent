"""An isolated API with in-memory storage, a movable clock and test-signed tokens.

Token lifetimes are checked by PyJWT against the real time, so tokens are issued
now; lesson rules use the injected clock, which starts at ``NOW``.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from lessons_api.app import create_app
from lessons_api.auth import TokenVerifier

ISSUER = "https://test.clerk.accounts.dev"
FRONTEND = "http://localhost:3000"
NOW = datetime(2026, 11, 2, 9, tzinfo=UTC)

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(
    sub: str,
    role: str | None = None,
    name: str | None = None,
    *,
    key: rsa.RSAPrivateKey = PRIVATE_KEY,
    issued: datetime | None = None,
    lifetime: timedelta = timedelta(hours=1),
    **claims: Any,
) -> str:
    """Sign a Clerk-shaped session token."""

    issued = issued or datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": sub,
        "iss": ISSUER,
        "azp": FRONTEND,
        "iat": int(issued.timestamp()),
        "nbf": int(issued.timestamp()),
        "exp": int((issued + lifetime).timestamp()),
        "name": name if name is not None else sub.title(),
        "email": f"{sub}@example.com",
        **claims,
    }
    if role is not None:
        payload["role"] = role
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "test"})


def auth(sub: str, role: str | None = None, **kwargs: Any) -> dict[str, str]:
    """Authorization header for a test user."""

    return {"Authorization": f"Bearer {token(sub, role, **kwargs)}"}


STUDENT = auth("alice")
OTHER_STUDENT = auth("bob")
TEACHER = auth("tom", "teacher", name="Tom Teacher")
OTHER_TEACHER = auth("tina", "teacher", name="Tina Teacher")


@dataclass
class Clock:
    """A clock tests can move forward."""

    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def client(clock: Clock) -> Iterator[TestClient]:
    verifier = TokenVerifier(ISSUER, [FRONTEND], lambda raw: PRIVATE_KEY.public_key())
    app = create_app(verifier, clock=clock, cors_origins=[FRONTEND])
    with TestClient(app) as test_client:
        yield test_client
    app.state.store.close()
