"""FastAPI application factory."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime

from lessons_api.auth import TokenVerifier, verified_caller
from lessons_api.models import Caller, CreateLesson, Lesson, Teacher
from lessons_api.service import (
    ConflictError,
    ForbiddenError,
    InvalidRequestError,
    LessonError,
    LessonService,
    NotFoundError,
)
from lessons_api.storage import Store

ERROR_STATUS: dict[type[LessonError], int] = {
    InvalidRequestError: 422,
    ForbiddenError: 403,
    NotFoundError: 404,
    ConflictError: 409,
}

ERROR_RESPONSES = {
    401: {"description": "Missing, invalid or expired Clerk session token"},
    403: {"description": "The caller's role may not do this"},
    404: {"description": "Lesson not found or not visible to the caller"},
    409: {"description": "The lesson's state does not allow this"},
}


def _service(request: Request) -> LessonService:
    return request.app.state.service


def current_caller(
    caller: Annotated[Caller, Depends(verified_caller)],
    service: Annotated[LessonService, Depends(_service)],
) -> Caller:
    """
    Authenticate the request and record the caller as a known user.

    Args:
        caller: The caller from the verified session token.
        service: The app's lesson service.

    Returns:
        The caller.
    """

    service.sign_in(caller)
    return caller


CallerDep = Annotated[Caller, Depends(current_caller)]
ServiceDep = Annotated[LessonService, Depends(_service)]


def create_app(
    verifier: TokenVerifier,
    database: Path | str = ":memory:",
    clock: Callable[[], datetime] | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """
    Create the lessons API with injectable token verification, storage and clock.

    Args:
        verifier: Verifies Clerk session tokens.
        database: SQLite file path, or ``:memory:`` for tests.
        clock: Returns the current time; defaults to the system clock in UTC.
        cors_origins: Browser origins allowed to call the API.

    Returns:
        The FastAPI app. ``app.state.store`` holds the store; close it on shutdown.
    """

    store = Store(database)
    app = FastAPI(
        title="Lessons API",
        version="2.0.0",
        description="Teachers open lesson slots, students book them, and either side cancels. Every request needs a Clerk session token as `Authorization: Bearer`; the token's `role` claim decides whether the caller is a teacher or a student. Times are ISO 8601; responses use UTC.",
    )
    app.state.store = store
    app.state.service = LessonService(store, clock or (lambda: datetime.now(UTC)))
    app.state.verifier = verifier
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or [],
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.exception_handler(LessonError)
    def lesson_error(request: Request, error: LessonError) -> JSONResponse:
        """Translate a rule violation into its HTTP status."""

        return JSONResponse({"detail": str(error)}, ERROR_STATUS[type(error)])

    @app.get("/me", operation_id="get_me", responses=ERROR_RESPONSES)
    def get_me(caller: CallerDep) -> Caller:
        """Return the signed-in user and their role."""

        return caller

    @app.get("/teachers", operation_id="list_teachers", responses=ERROR_RESPONSES)
    def list_teachers(caller: CallerDep, service: ServiceDep) -> list[Teacher]:
        """List teachers who have signed in at least once."""

        return service.list_teachers()

    @app.get("/lessons/mine", operation_id="list_my_lessons", responses=ERROR_RESPONSES)
    def list_my_lessons(
        caller: CallerDep, service: ServiceDep, include_past: bool = False
    ) -> list[Lesson]:
        """Students get lessons they booked; teachers get lessons they created, in any status."""

        return service.my_lessons(caller, include_past)

    @app.get(
        "/lessons/available",
        operation_id="list_available_lessons",
        responses=ERROR_RESPONSES,
    )
    def list_available_lessons(
        caller: CallerDep,
        service: ServiceDep,
        starts_after: AwareDatetime | None = None,
        starts_before: AwareDatetime | None = None,
        teacher_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[Lesson]:
        """Students only: open future lessons, optionally in a time window or for one teacher."""

        return service.available_lessons(
            caller, starts_after, starts_before, teacher_id, limit
        )

    @app.get(
        "/lessons/{lesson_id}", operation_id="get_lesson", responses=ERROR_RESPONSES
    )
    def get_lesson(lesson_id: str, caller: CallerDep, service: ServiceDep) -> Lesson:
        """A lesson the caller teaches or booked, or an open future lesson for students."""

        return service.get_lesson(caller, lesson_id)

    @app.post(
        "/lessons",
        operation_id="create_lesson",
        status_code=201,
        responses=ERROR_RESPONSES,
    )
    def create_lesson(
        request: CreateLesson, caller: CallerDep, service: ServiceDep
    ) -> Lesson:
        """Teachers only: open a lesson slot that does not overlap their other lessons."""

        return service.create_lesson(caller, request)

    @app.post(
        "/lessons/{lesson_id}/book",
        operation_id="book_lesson",
        responses=ERROR_RESPONSES,
    )
    def book_lesson(lesson_id: str, caller: CallerDep, service: ServiceDep) -> Lesson:
        """Students only: book an open lesson. Repeating the call returns the same booking."""

        return service.book_lesson(caller, lesson_id)

    @app.post(
        "/lessons/{lesson_id}/cancel",
        operation_id="cancel_lesson",
        responses=ERROR_RESPONSES,
    )
    def cancel_lesson(lesson_id: str, caller: CallerDep, service: ServiceDep) -> Lesson:
        """Students cancel their booking and the lesson reopens; teachers cancel the lesson."""

        return service.cancel_lesson(caller, lesson_id)

    return app
