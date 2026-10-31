"""HTTP client for the lessons API, acting as the signed-in user."""

import logging
from datetime import datetime
from typing import Any

import httpx
from pydantic import TypeAdapter, ValidationError

from tech_support_agent.config import get_settings
from tech_support_agent.context import SignedInUser
from tech_support_agent.lessons.models import Lesson, Teacher

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0

SESSION_EXPIRED = (
    "The user's session expired while you were working. Ask them to send "
    "their message again."
)
UNAVAILABLE = "The lessons service is not reachable right now. Try again later."
SERVER_ERROR = "The lessons service had an internal problem. Try again later."

_LESSON = TypeAdapter(Lesson)
_LESSONS = TypeAdapter(list[Lesson])
_TEACHERS = TypeAdapter(list[Teacher])


class LessonsApiError(Exception):
    """A lessons API call failed; ``message`` is safe to show the model and user."""

    def __init__(self, status: int, message: str) -> None:
        """
        Create the error.

        Args:
            status: HTTP status, or 0 when the API was unreachable.
            message: What went wrong, in plain words.
        """

        super().__init__(message)
        self.status = status
        self.message = message


def default_transport() -> httpx.AsyncBaseTransport | None:
    """
    Transport for new clients; None uses the network.

    Returns:
        None. Tests replace this to serve the API from memory.
    """

    return None


def _parse[T](adapter: TypeAdapter[T], data: Any) -> T:
    try:
        return adapter.validate_python(data)
    except ValidationError:
        logger.warning("Unexpected lessons API response shape", exc_info=True)
        raise LessonsApiError(502, SERVER_ERROR) from None


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError, AttributeError:
        return response.reason_phrase or f"HTTP {response.status_code}"
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list) and detail and isinstance(detail[0], dict):
        # FastAPI validation errors: report the first problem.
        return str(detail[0].get("msg", detail[0]))
    return f"HTTP {response.status_code}"


class LessonsClient:
    """Calls the lessons API with the user's Clerk session token."""

    def __init__(
        self,
        base_url: str,
        user: SignedInUser,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """
        Configure the client.

        Args:
            base_url: The lessons API origin.
            user: The signed-in user whose token authorizes every call.
            transport: Optional transport, for tests.
        """

        self._base_url = base_url
        self._user = user
        self._transport = transport

    async def my_lessons(self, include_past: bool = False) -> list[Lesson]:
        """
        List the student's bookings or the teacher's lessons.

        Args:
            include_past: Also return lessons that have ended.

        Returns:
            The lessons, ordered by start time.
        """

        data = await self._read(
            "/lessons/mine", {"include_past": str(include_past).lower()}
        )
        return _parse(_LESSONS, data)

    async def teachers(self) -> list[Teacher]:
        """
        List teachers who have signed in.

        Returns:
            The teachers.
        """

        return _parse(_TEACHERS, await self._read("/teachers"))

    async def available_lessons(
        self,
        starts_after: datetime,
        starts_before: datetime,
        teacher_id: str | None = None,
    ) -> list[Lesson]:
        """
        Find open lessons a student can book in a time window.

        Args:
            starts_after: Earliest start, timezone-aware.
            starts_before: Latest start (exclusive), timezone-aware.
            teacher_id: Only this teacher's lessons, if given.

        Returns:
            Open lessons, ordered by start time.
        """

        params = {
            "starts_after": starts_after.isoformat(),
            "starts_before": starts_before.isoformat(),
        }
        if teacher_id:
            params["teacher_id"] = teacher_id
        return _parse(_LESSONS, await self._read("/lessons/available", params))

    async def get_lesson(self, lesson_id: str) -> Lesson:
        """
        Fetch one lesson the user may see.

        Args:
            lesson_id: The lesson ID.

        Returns:
            The lesson.
        """

        return _parse(_LESSON, await self._read(f"/lessons/{lesson_id}"))

    async def create_lesson(
        self, subject: str, starts_at: datetime, duration_minutes: int
    ) -> Lesson:
        """
        Open a lesson as the teacher. Never retried.

        Args:
            subject: What the lesson is about.
            starts_at: Start time, timezone-aware.
            duration_minutes: Length of the lesson.

        Returns:
            The new open lesson.
        """

        body = {
            "subject": subject,
            "starts_at": starts_at.isoformat(),
            "duration_minutes": duration_minutes,
        }
        return _parse(_LESSON, await self._write("/lessons", body))

    async def book_lesson(self, lesson_id: str) -> Lesson:
        """
        Book an open lesson as the student. Never retried.

        Args:
            lesson_id: The lesson ID.

        Returns:
            The booked lesson.
        """

        return _parse(_LESSON, await self._write(f"/lessons/{lesson_id}/book"))

    async def cancel_lesson(self, lesson_id: str) -> Lesson:
        """
        Cancel a booking (student) or a lesson (teacher). Never retried.

        Args:
            lesson_id: The lesson ID.

        Returns:
            The lesson after cancellation.
        """

        return _parse(_LESSON, await self._write(f"/lessons/{lesson_id}/cancel"))

    async def _read(self, path: str, params: dict[str, str] | None = None) -> Any:
        try:
            return await self._send("GET", path, params=params)
        except LessonsApiError as error:
            if error.status != 0:
                raise
            logger.info("Retrying GET %s after a network error", path)
            return await self._send("GET", path, params=params)

    async def _write(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return await self._send("POST", path, json=body)

    async def _send(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {self._user.token.get_secret_value()}"}
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = await client.request(
                    method, path, params=params, json=json, headers=headers
                )
        except httpx.TransportError as error:
            logger.warning("%s %s failed: %s", method, path, type(error).__name__)
            raise LessonsApiError(0, UNAVAILABLE) from None
        if response.status_code == 401:
            raise LessonsApiError(401, SESSION_EXPIRED)
        if response.status_code >= 500:
            logger.warning("%s %s returned %s", method, path, response.status_code)
            raise LessonsApiError(response.status_code, SERVER_ERROR)
        if response.status_code >= 400:
            raise LessonsApiError(response.status_code, _detail(response))
        try:
            return response.json()
        except ValueError:
            raise LessonsApiError(response.status_code, SERVER_ERROR) from None


def client_for(user: SignedInUser) -> LessonsClient:
    """
    Build a client for the configured lessons API, acting as ``user``.

    Args:
        user: The signed-in user.

    Returns:
        The client.
    """

    return LessonsClient(get_settings().lessons_api_url, user, default_transport())
