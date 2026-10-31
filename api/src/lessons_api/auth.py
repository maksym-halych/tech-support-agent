"""Caller authentication."""

from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer()

DEMO_USERS = {"demo-alice": "alice", "demo-bob": "bob"}


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)],
) -> str:
    """
    Authenticate the two explicitly documented local demo identities.

    Args:
        credentials: The bearer token from the ``Authorization`` header.

    Returns:
        The caller's user ID.

    Raises:
        HTTPException: 401 if the token is not a known demo token.
    """

    if credentials.credentials not in DEMO_USERS:
        raise HTTPException(401, "Unknown demo token")
    return DEMO_USERS[credentials.credentials]
