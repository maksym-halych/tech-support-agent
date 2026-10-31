from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from lessons_api.app import create_app
from lessons_api.auth import TokenVerifier
from tests.conftest import FRONTEND, ISSUER, OTHER_KEY, PRIVATE_KEY, auth, token


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (None, "student"),
        ("teacher", "teacher"),
        ("student", "student"),
        ("Teacher", "student"),
        ("admin", "student"),
        ("", "student"),
    ],
)
def test_role_comes_only_from_exact_teacher_claim(
    client: TestClient, role: str | None, expected: str
) -> None:
    response = client.get("/me", headers=auth("alice", role))
    assert response.status_code == 200
    assert response.json() == {
        "id": "alice",
        "name": "Alice",
        "email": "alice@example.com",
        "role": expected,
    }


def test_name_falls_back_to_email(client: TestClient) -> None:
    me = client.get("/me", headers=auth("alice", name="")).json()
    assert me["name"] == "alice@example.com"


def test_azp_may_be_absent(client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {token('alice', azp=None)}"}
    assert client.get("/me", headers=headers).status_code == 200


def _bearer(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        _bearer("not-a-jwt"),
        _bearer(token("alice", key=OTHER_KEY)),
        _bearer(token("alice", iss="https://evil.clerk.accounts.dev")),
        _bearer(token("alice", azp="https://evil.example")),
        _bearer(
            token(
                "alice",
                issued=datetime.now(UTC) - timedelta(minutes=5),
                lifetime=timedelta(seconds=60),
            )
        ),
        _bearer(token("alice", nbf=int(datetime.now(UTC).timestamp()) + 600)),
        {"Authorization": "Basic YWxpY2U6c2VjcmV0"},
    ],
    ids=[
        "missing",
        "malformed",
        "wrong-key",
        "wrong-issuer",
        "wrong-azp",
        "expired",
        "not-yet-valid",
        "not-bearer",
    ],
)
def test_invalid_tokens_get_401(client: TestClient, headers: dict[str, str]) -> None:
    response = client.get("/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_token_without_sub_gets_401(client: TestClient) -> None:
    payload = jwt.decode(
        token("alice"), options={"verify_signature": False}, algorithms=["RS256"]
    )
    del payload["sub"]
    raw = jwt.encode(payload, PRIVATE_KEY, algorithm="RS256")
    assert client.get("/me", headers=_bearer(raw)).status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/me",
        "/teachers",
        "/lessons/mine",
        "/lessons/available",
        "/lessons/some-id",
    ],
)
def test_every_read_requires_a_token(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(
    "path", ["/lessons", "/lessons/some-id/book", "/lessons/some-id/cancel"]
)
def test_every_write_requires_a_token(client: TestClient, path: str) -> None:
    assert client.post(path, json={}).status_code == 401


def test_unreachable_jwks_gets_503() -> None:
    def unreachable(raw: str) -> None:
        raise jwt.PyJWKClientConnectionError("Fail to fetch data from the url")

    app = create_app(TokenVerifier(ISSUER, [FRONTEND], unreachable))
    with TestClient(app) as client:
        assert client.get("/me", headers=auth("alice")).status_code == 503
    app.state.store.close()


def test_users_are_recorded_with_their_latest_role(client: TestClient) -> None:
    client.get("/me", headers=auth("tom", "teacher", name="Tom Teacher"))
    client.get("/me", headers=auth("alice"))
    teachers = client.get("/teachers", headers=auth("alice")).json()
    assert teachers == [{"id": "tom", "name": "Tom Teacher"}]
    client.get("/me", headers=auth("tom", name="Tom Teacher"))
    assert client.get("/teachers", headers=auth("alice")).json() == []


def test_cors_allows_the_frontend_origin(client: TestClient) -> None:
    response = client.options(
        "/lessons/mine",
        headers={
            "Origin": FRONTEND,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND
