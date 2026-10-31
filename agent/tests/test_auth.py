from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from langgraph_sdk import Auth

from tech_support_agent import auth as agent_auth
from tech_support_agent.clerk import InvalidTokenError, TokenVerifier
from tech_support_agent.config import Settings
from tech_support_agent.context import signed_in_user

ISSUER = "https://test.clerk.accounts.dev"
FRONTEND = "http://localhost:3000"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(
    sub: str = "user_alice",
    *,
    key: rsa.RSAPrivateKey = KEY,
    lifetime: timedelta = timedelta(minutes=5),
    issued: datetime | None = None,
    **claims: Any,
) -> str:
    issued = issued or datetime.now(UTC)
    payload = {
        "sub": sub,
        "iss": ISSUER,
        "azp": FRONTEND,
        "iat": int(issued.timestamp()),
        "nbf": int(issued.timestamp()),
        "exp": int((issued + lifetime).timestamp()),
        "name": "Alice",
        "email": "alice@example.com",
        **claims,
    }
    return jwt.encode(payload, key, algorithm="RS256")


@pytest.fixture
def verifier(monkeypatch: pytest.MonkeyPatch) -> TokenVerifier:
    test_verifier = TokenVerifier(ISSUER, [FRONTEND], lambda raw: KEY.public_key())
    monkeypatch.setattr(agent_auth, "get_verifier", lambda: test_verifier)
    return test_verifier


@pytest.mark.parametrize(
    ("role", "expected"),
    [(None, "student"), ("teacher", "teacher"), ("Teacher", "student")],
)
def test_role_comes_only_from_exact_teacher_claim(
    verifier: TokenVerifier, role: str | None, expected: str
) -> None:
    claims = {"role": role} if role is not None else {}
    assert verifier.verify(token(**claims)).role == expected


@pytest.mark.parametrize(
    "raw",
    [
        "not-a-jwt",
        token(key=OTHER_KEY),
        token(iss="https://evil.clerk.accounts.dev"),
        token(azp="https://evil.example"),
        token(
            issued=datetime.now(UTC) - timedelta(minutes=5),
            lifetime=timedelta(seconds=60),
        ),
    ],
    ids=["malformed", "wrong-key", "wrong-issuer", "wrong-azp", "expired"],
)
def test_invalid_tokens_are_rejected(verifier: TokenVerifier, raw: str) -> None:
    with pytest.raises(InvalidTokenError):
        verifier.verify(raw)


async def test_authenticate_returns_the_user_and_their_token(
    verifier: TokenVerifier,
) -> None:
    raw = token(role="teacher")

    user = await agent_auth.authenticate(f"Bearer {raw}")

    assert user == {
        "identity": "user_alice",
        "display_name": "Alice",
        "email": "alice@example.com",
        "role": "teacher",
        "access_token": raw,
        "permissions": [],
    }


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Basic abc", f"Bearer {token(key=OTHER_KEY)}"],
    ids=["none", "empty", "no-token", "not-bearer", "invalid"],
)
async def test_authenticate_rejects_with_401(
    verifier: TokenVerifier, header: str | None
) -> None:
    with pytest.raises(Auth.exceptions.HTTPException) as raised:
        await agent_auth.authenticate(header)
    assert raised.value.status_code == 401


def test_missing_issuer_setting_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_auth.get_verifier.cache_clear()
    monkeypatch.setattr(agent_auth, "get_settings", lambda: Settings(_env_file=None))
    with pytest.raises(RuntimeError, match="CLERK_ISSUER is not set"):
        agent_auth.get_verifier()
    agent_auth.get_verifier.cache_clear()


class _User:
    identity = "user_alice"
    display_name = "Alice"


def _ctx(resource: str, action: str) -> Auth.types.AuthContext:
    return Auth.types.AuthContext(
        user=_User(), permissions=[], resource=resource, action=action
    )


async def test_new_threads_and_runs_are_stamped_with_their_owner() -> None:
    thread: dict[str, Any] = {"metadata": {"title": "x"}}
    run: dict[str, Any] = {}

    thread_filter = await agent_auth.on_thread_create(_ctx("threads", "create"), thread)
    run_filter = await agent_auth.on_run_create(_ctx("threads", "create_run"), run)

    assert thread["metadata"] == {"title": "x", "owner": "user_alice"}
    assert run["metadata"] == {"owner": "user_alice"}
    assert thread_filter == run_filter == {"owner": "user_alice"}


async def test_owner_is_stamped_into_the_servers_own_metadata_dict() -> None:
    for metadata in ({}, {"owner": "user_bob"}):
        value: dict[str, Any] = {"metadata": metadata}
        await agent_auth.on_thread_create(_ctx("threads", "create"), value)
        # The server reads the dict it passed in, so it must be the same object.
        assert value["metadata"] is metadata
        assert metadata == {"owner": "user_alice"}
    missing: dict[str, Any] = {"metadata": None}
    await agent_auth.on_run_create(_ctx("threads", "create_run"), missing)
    assert missing["metadata"] == {"owner": "user_alice"}


async def test_thread_reads_are_filtered_by_owner() -> None:
    for action in ("read", "update", "delete", "search"):
        assert await agent_auth.on_thread_access(_ctx("threads", action), {}) == {
            "owner": "user_alice"
        }


async def test_assistants_are_read_only() -> None:
    assert await agent_auth.on_assistant_read(_ctx("assistants", "read"), {})
    with pytest.raises(Auth.exceptions.HTTPException) as raised:
        await agent_auth.deny_everything_else(_ctx("assistants", "create"), {})
    assert raised.value.status_code == 403


async def test_store_namespaces_are_scoped_to_the_user() -> None:
    value: dict[str, Any] = {"namespace": ("notes",)}
    await agent_auth.on_store(_ctx("store", "put"), value)
    assert value["namespace"] == ("user_alice", "notes")


def test_handlers_are_registered_for_every_thread_action() -> None:
    registered = {key for key in agent_auth.auth._handlers}
    for action in ("create", "read", "update", "delete", "search", "create_run"):
        assert ("threads", action) in registered
    assert ("assistants", "read") in registered
    assert ("assistants", "search") in registered


def test_signed_in_user_reads_the_server_user() -> None:
    from langgraph.runtime import ServerInfo

    from tests.conftest import ServerUser

    user = ServerUser(
        "user_tom", "Tom", {"role": "teacher", "access_token": "secret-token"}
    )
    signed_in = signed_in_user(ServerInfo(assistant_id="a", graph_id="g", user=user))

    assert signed_in is not None
    assert (signed_in.id, signed_in.name, signed_in.role) == (
        "user_tom",
        "Tom",
        "teacher",
    )
    assert signed_in.token.get_secret_value() == "secret-token"
    assert "secret-token" not in repr(signed_in)
    assert "token" not in signed_in.model_dump()
    studio = ServerUser("langgraph-studio-user", "Studio")
    assert (
        signed_in_user(ServerInfo(assistant_id="a", graph_id="g", user=studio)) is None
    )
    assert signed_in_user(None) is None
