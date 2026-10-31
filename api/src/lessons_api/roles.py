"""Command-line entry point: list Clerk users and give one the teacher or student role.

The role lives in the user's Clerk public metadata (``{"role": "teacher"}``),
which the customised session token carries as its ``role`` claim. The change
reaches the API with the user's next session token, so they sign in again.
"""

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any, get_args
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from lessons_api.models import Role

CLERK_API_URL = "https://api.clerk.com/v1"

# Sends a request to the Clerk Backend API and returns the decoded JSON body.
Send = Callable[[str, str, dict[str, Any] | None], Any]


class ClerkAdminError(Exception):
    """The Clerk Backend API refused or could not serve a request."""


class AdminSettings(BaseSettings):
    """The Clerk secret key, from the environment or the frontend's ``.env``."""

    # The frontend already holds the instance's secret key, so it is read from
    # there rather than copied. Paths are relative to api/, where uv runs this.
    model_config = SettingsConfigDict(
        env_file=("../frontend/.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    clerk_secret_key: SecretStr


class ClerkUser(BaseModel):
    """The parts of a Clerk user this command shows."""

    id: str
    email: str
    name: str
    role: Role


def to_clerk_user(data: dict[str, Any]) -> ClerkUser:
    """
    Read a user from a Clerk Backend API user object.

    Args:
        data: The user object.

    Returns:
        The user, with ``student`` for any role other than ``teacher``.
    """

    emails = {
        item["id"]: item["email_address"] for item in data.get("email_addresses", [])
    }
    email = emails.get(data.get("primary_email_address_id") or "", "")
    name = " ".join(
        part for part in (data.get("first_name"), data.get("last_name")) if part
    )
    role = (data.get("public_metadata") or {}).get("role")
    return ClerkUser(
        id=data["id"],
        email=email,
        name=name,
        role="teacher" if role == "teacher" else "student",
    )


def clerk_sender(secret_key: SecretStr) -> Send:
    """
    Build a sender that calls the Clerk Backend API with the secret key.

    Args:
        secret_key: The instance's ``sk_...`` secret key.

    Returns:
        A function taking the method, the path under ``/v1`` and an optional JSON body.
    """

    def send(method: str, path: str, body: dict[str, Any] | None) -> Any:
        request = Request(
            f"{CLERK_API_URL}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {secret_key.get_secret_value()}",
                "Content-Type": "application/json",
                # Clerk's edge rejects urllib's default user agent.
                "User-Agent": "lessons-api-role",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                return json.load(response)
        except HTTPError as error:
            raise ClerkAdminError(_clerk_error_message(error)) from None
        except URLError as error:
            raise ClerkAdminError(f"Cannot reach Clerk: {error.reason}") from None

    return send


def _clerk_error_message(error: HTTPError) -> str:
    try:
        errors = json.load(error).get("errors") or []
    except ValueError:
        errors = []
    detail = errors[0].get("long_message") or errors[0].get("message") if errors else ""
    return f"Clerk answered {error.code}: {detail or error.reason}"


def list_users(send: Send) -> list[ClerkUser]:
    """
    Return the instance's users, newest first.

    Args:
        send: The Clerk Backend API sender.

    Returns:
        Up to the 100 newest users.
    """

    query = urlencode({"limit": 100, "order_by": "-created_at"})
    return [to_clerk_user(item) for item in send("GET", f"/users?{query}", None)]


def find_user(send: Send, email: str) -> ClerkUser | None:
    """
    Find a user by email address.

    Args:
        send: The Clerk Backend API sender.
        email: Any of the user's email addresses.

    Returns:
        The user, or None if no user has that address.
    """

    query = urlencode({"email_address": email})
    matches = send("GET", f"/users?{query}", None)
    return to_clerk_user(matches[0]) if matches else None


def set_role(send: Send, user_id: str, role: Role) -> ClerkUser:
    """
    Set the user's role in their public metadata, keeping its other keys.

    Args:
        send: The Clerk Backend API sender.
        user_id: The Clerk user ID.
        role: The new role.

    Returns:
        The updated user.
    """

    # The metadata endpoint deep-merges, so other public metadata survives.
    path = f"/users/{quote(user_id, safe='')}/metadata"
    return to_clerk_user(send("PATCH", path, {"public_metadata": {"role": role}}))


def _format(user: ClerkUser) -> str:
    return f"{user.role:<8} {user.email or '(no email)':<40} {user.name or '-':<24} {user.id}"


def main() -> None:
    """List the users, or set one user's role by email."""

    parser = argparse.ArgumentParser(
        description="List Clerk users, or give one the teacher or student role"
    )
    parser.add_argument("email", nargs="?", help="the user's email; omit to list users")
    parser.add_argument("--role", choices=get_args(Role), default="teacher")
    args = parser.parse_args()

    try:
        send = clerk_sender(AdminSettings().clerk_secret_key)
    except ValidationError:
        sys.exit("CLERK_SECRET_KEY is not set: it is read from frontend/.env.")

    try:
        if args.email is None:
            for user in list_users(send):
                print(_format(user))
            return
        user = find_user(send, args.email)
        if user is None:
            sys.exit(f"No Clerk user has the email {args.email}.")
        print(_format(set_role(send, user.id, args.role)))
    except ClerkAdminError as error:
        sys.exit(str(error))
    print(
        "Sign out and back in as this user so their session token carries the new role."
    )


if __name__ == "__main__":
    main()
