"""Agent Server authentication and per-user thread isolation.

Loaded by Agent Server from ``langgraph.json`` (``auth.path``). Every request
must carry the user's Clerk session token; threads and runs belong to the
user who created them.
"""

import asyncio
import logging
from functools import cache

from langgraph_sdk import Auth

from tech_support_agent.clerk import (
    InvalidTokenError,
    KeysUnavailableError,
    TokenVerifier,
    jwks_key_resolver,
)
from tech_support_agent.config import get_settings

logger = logging.getLogger(__name__)

auth = Auth()

OWNER_KEY = "owner"


@cache
def get_verifier() -> TokenVerifier:
    """
    Build the token verifier from settings on first use.

    Returns:
        The process-wide verifier.

    Raises:
        RuntimeError: If ``CLERK_ISSUER`` is not configured.
    """

    settings = get_settings()
    if not settings.clerk_issuer:
        raise RuntimeError(
            "CLERK_ISSUER is not set: add it to agent/.env (see agent/.env.example)."
        )
    return TokenVerifier(
        settings.clerk_issuer,
        settings.clerk_authorized_parties,
        jwks_key_resolver(settings.clerk_issuer),
    )


@auth.authenticate
async def authenticate(authorization: str | None) -> Auth.types.MinimalUserDict:
    """
    Verify the Clerk session token on every request.

    Args:
        authorization: The ``Authorization`` header.

    Returns:
        The user: ``identity`` is the Clerk user ID; ``role`` and the raw
        ``access_token`` travel with each run for the lessons API client.

    Raises:
        Auth.exceptions.HTTPException: 401 for a missing or invalid token, 503
            when Clerk's keys cannot be fetched.
    """

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Auth.exceptions.HTTPException(401, "Missing session token")
    try:
        # PyJWKClient fetches keys with blocking I/O on a cache miss.
        user = await asyncio.to_thread(get_verifier().verify, token.strip())
    except InvalidTokenError as error:
        logger.info("Rejected session token: %s", error)
        raise Auth.exceptions.HTTPException(401, "Invalid session token") from None
    except KeysUnavailableError as error:
        logger.warning("Cannot fetch Clerk signing keys: %s", error)
        raise Auth.exceptions.HTTPException(
            503, "Cannot verify the session token right now"
        ) from None
    return {
        "identity": user.id,
        "display_name": user.name,
        "email": user.email,
        "role": user.role,
        "access_token": user.token.get_secret_value(),
        "permissions": [],
    }


@auth.on.threads.create
async def on_thread_create(
    ctx: Auth.types.AuthContext, value: Auth.types.on.threads.create.value
) -> Auth.types.FilterType:
    """Stamp a new thread with its owner."""

    metadata = value.setdefault("metadata", {}) or {}
    metadata[OWNER_KEY] = ctx.user.identity
    value["metadata"] = metadata
    return {OWNER_KEY: ctx.user.identity}


@auth.on.threads.create_run
async def on_run_create(
    ctx: Auth.types.AuthContext, value: Auth.types.on.threads.create_run.value
) -> Auth.types.FilterType:
    """Stamp a new run with its owner; runs only start on the owner's threads."""

    metadata = value.setdefault("metadata", {}) or {}
    metadata[OWNER_KEY] = ctx.user.identity
    value["metadata"] = metadata
    return {OWNER_KEY: ctx.user.identity}


@auth.on.threads.read
@auth.on.threads.update
@auth.on.threads.delete
@auth.on.threads.search
async def on_thread_access(
    ctx: Auth.types.AuthContext, value: dict
) -> Auth.types.FilterType:
    """Let users see and change only their own threads."""

    return {OWNER_KEY: ctx.user.identity}


@auth.on.assistants.read
@auth.on.assistants.search
async def on_assistant_read(ctx: Auth.types.AuthContext, value: dict) -> bool:
    """Every signed-in user may use the deployed assistants."""

    return True


@auth.on.store
async def on_store(ctx: Auth.types.AuthContext, value: dict) -> None:
    """Scope every store namespace under the user's identity."""

    value["namespace"] = (ctx.user.identity, *(value.get("namespace") or ()))


@auth.on
async def deny_everything_else(ctx: Auth.types.AuthContext, value: dict) -> bool:
    """Deny actions without an explicit rule, e.g. changing assistants or crons."""

    raise Auth.exceptions.HTTPException(
        403, f"{ctx.resource}.{ctx.action} is not allowed"
    )
