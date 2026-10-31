"""Lesson rules: who may see, create, book and cancel which lessons."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from lessons_api.models import (
    MAX_DURATION_MINUTES,
    Caller,
    CreateLesson,
    Lesson,
    Person,
    Role,
    Teacher,
)
from lessons_api.storage import Store

Clock = Callable[[], datetime]


class LessonError(Exception):
    """A request the lesson rules do not allow; the message is safe to show."""


class InvalidRequestError(LessonError):
    """The request is well-formed but its values are not acceptable."""


class ForbiddenError(LessonError):
    """The caller's role may not perform this action."""


class NotFoundError(LessonError):
    """The lesson does not exist or the caller may not see it."""


class ConflictError(LessonError):
    """The lesson's current state does not allow the action."""


def _require_role(caller: Caller, role: Role, action: str) -> None:
    if caller.role != role:
        raise ForbiddenError(f"Only {role}s can {action}")


def _is_student(lesson: Lesson, caller: Caller) -> bool:
    return lesson.student is not None and lesson.student.id == caller.id


def _overlaps(lesson: Lesson, starts_at: datetime, ends_at: datetime) -> bool:
    return lesson.starts_at < ends_at and starts_at < lesson.ends_at


class LessonService:
    """Applies the lesson state machine on top of the store."""

    def __init__(self, store: Store, clock: Clock) -> None:
        """
        Create the service.

        Args:
            store: Persistence for users and lessons.
            clock: Returns the current time as an aware datetime.
        """

        self._store = store
        self._clock = clock

    def sign_in(self, caller: Caller) -> None:
        """
        Record that the caller made a request, with their latest claims.

        Args:
            caller: The verified caller.
        """

        self._store.upsert_user(caller, self._clock())

    def list_teachers(self) -> list[Teacher]:
        """
        List teachers who have signed in.

        Returns:
            The known teachers.
        """

        return self._store.list_teachers()

    def my_lessons(self, caller: Caller, include_past: bool) -> list[Lesson]:
        """
        List the lessons a student booked or a teacher created.

        Args:
            caller: The verified caller.
            include_past: Also return lessons that have already ended.

        Returns:
            The caller's lessons, ordered by start time.
        """

        now = self._clock()
        starts_from = (
            None if include_past else now - timedelta(minutes=MAX_DURATION_MINUTES)
        )
        if caller.role == "teacher":
            lessons = self._store.lessons_for_teacher(caller.id, starts_from)
        else:
            lessons = self._store.lessons_for_student(caller.id, starts_from)
        return [lesson for lesson in lessons if include_past or lesson.ends_at > now]

    def available_lessons(
        self,
        caller: Caller,
        starts_after: datetime | None,
        starts_before: datetime | None,
        teacher_id: str | None,
        limit: int,
    ) -> list[Lesson]:
        """
        Find open future lessons a student can book.

        Args:
            caller: The verified caller; must be a student.
            starts_after: Earliest start time; defaults to now.
            starts_before: Latest start time, exclusive.
            teacher_id: Only this teacher's lessons, if given.
            limit: Maximum number of lessons.

        Returns:
            Open lessons ordered by start time.

        Raises:
            ForbiddenError: If the caller is a teacher.
        """

        _require_role(caller, "student", "search for lessons to book")
        now = self._clock()
        starts_from = max(starts_after, now) if starts_after else now
        return [
            lesson
            for lesson in self._store.open_lessons(
                starts_from, starts_before, teacher_id, limit
            )
            if lesson.starts_at > now
        ]

    def get_lesson(self, caller: Caller, lesson_id: str) -> Lesson:
        """
        Return a lesson the caller teaches, booked, or could book.

        Args:
            caller: The verified caller.
            lesson_id: The lesson ID.

        Returns:
            The lesson.

        Raises:
            NotFoundError: If the lesson does not exist or is not visible to the caller.
        """

        lesson = self._store.get_lesson(lesson_id)
        if lesson is None or not self._can_see(caller, lesson):
            raise NotFoundError("Lesson not found")
        return lesson

    def create_lesson(self, caller: Caller, request: CreateLesson) -> Lesson:
        """
        Open a new lesson slot for the calling teacher.

        Args:
            caller: The verified caller; must be a teacher.
            request: Subject, start time and duration.

        Returns:
            The new open lesson.

        Raises:
            ForbiddenError: If the caller is a student.
            InvalidRequestError: If the lesson would start in the past.
            ConflictError: If it overlaps another of the teacher's lessons.
        """

        _require_role(caller, "teacher", "create lessons")
        now = self._clock()
        starts_at = request.starts_at.astimezone(UTC).replace(microsecond=0)
        if starts_at <= now:
            raise InvalidRequestError("A lesson must start in the future")
        lesson = Lesson(
            id=str(uuid4()),
            subject=request.subject,
            starts_at=starts_at,
            duration_minutes=request.duration_minutes,
            status="open",
            teacher=Person(id=caller.id, name=caller.name),
            student=None,
            created_at=now.astimezone(UTC).replace(microsecond=0),
            cancelled_at=None,
        )
        with self._store.transaction():
            nearby = self._store.lessons_for_teacher(
                caller.id, starts_at - timedelta(minutes=MAX_DURATION_MINUTES)
            )
            for other in nearby:
                if other.status != "cancelled" and _overlaps(
                    other, lesson.starts_at, lesson.ends_at
                ):
                    raise ConflictError(
                        f"Overlaps your lesson {other.subject!r} starting at "
                        f"{other.starts_at.isoformat()}"
                    )
            self._store.insert_lesson(lesson)
        return lesson

    def book_lesson(self, caller: Caller, lesson_id: str) -> Lesson:
        """
        Book an open lesson for the calling student.

        Booking a lesson the caller has already booked returns it unchanged.

        Args:
            caller: The verified caller; must be a student.
            lesson_id: The lesson ID.

        Returns:
            The booked lesson.

        Raises:
            ForbiddenError: If the caller is a teacher.
            NotFoundError: If the lesson does not exist.
            ConflictError: If it is booked by someone else, cancelled, started,
                or overlaps another of the caller's bookings.
        """

        _require_role(caller, "student", "book lessons")
        with self._store.transaction():
            lesson = self._store.get_lesson(lesson_id)
            if lesson is None:
                raise NotFoundError("Lesson not found")
            if lesson.status == "booked" and _is_student(lesson, caller):
                return lesson
            if lesson.status == "cancelled":
                raise ConflictError("The teacher cancelled this lesson")
            if lesson.status == "booked":
                raise ConflictError("Someone else has already booked this lesson")
            if lesson.starts_at <= self._clock():
                raise ConflictError("This lesson has already started")
            for other in self._store.lessons_for_student(
                caller.id, lesson.starts_at - timedelta(minutes=MAX_DURATION_MINUTES)
            ):
                if other.status == "booked" and _overlaps(
                    other, lesson.starts_at, lesson.ends_at
                ):
                    raise ConflictError(
                        f"You already have {other.subject!r} at "
                        f"{other.starts_at.isoformat()}"
                    )
            self._store.update_lesson(lesson.id, "booked", caller.id)
            return lesson.model_copy(
                update={"status": "booked", "student": self._me(caller)}
            )

    def cancel_lesson(self, caller: Caller, lesson_id: str) -> Lesson:
        """
        Cancel a booking (student) or a lesson (teacher).

        A student's cancellation reopens the lesson for others. A teacher's
        cancellation is final; cancelling an already cancelled lesson returns it
        unchanged.

        Args:
            caller: The verified caller.
            lesson_id: The lesson ID.

        Returns:
            The lesson after cancellation.

        Raises:
            NotFoundError: If the caller neither teaches nor booked the lesson.
            ConflictError: If the lesson has started or is already cancelled
                by the teacher (student only).
        """

        with self._store.transaction():
            lesson = self._store.get_lesson(lesson_id)
            if caller.role == "teacher":
                if lesson is None or lesson.teacher.id != caller.id:
                    raise NotFoundError("Lesson not found")
                return self._cancel_as_teacher(lesson)
            if lesson is None or not _is_student(lesson, caller):
                raise NotFoundError("Booking not found")
            return self._cancel_as_student(lesson)

    def _cancel_as_teacher(self, lesson: Lesson) -> Lesson:
        if lesson.status == "cancelled":
            return lesson
        now = self._clock()
        if lesson.starts_at <= now:
            raise ConflictError("This lesson has already started")
        cancelled_at = now.astimezone(UTC).replace(microsecond=0)
        self._store.update_lesson(
            lesson.id,
            "cancelled",
            lesson.student.id if lesson.student else None,
            cancelled_at,
        )
        return lesson.model_copy(
            update={"status": "cancelled", "cancelled_at": cancelled_at}
        )

    def _cancel_as_student(self, lesson: Lesson) -> Lesson:
        if lesson.status == "cancelled":
            raise ConflictError("The teacher already cancelled this lesson")
        if lesson.starts_at <= self._clock():
            raise ConflictError("This lesson has already started")
        self._store.update_lesson(lesson.id, "open", None)
        return lesson.model_copy(update={"status": "open", "student": None})

    def _can_see(self, caller: Caller, lesson: Lesson) -> bool:
        if caller.role == "teacher":
            return lesson.teacher.id == caller.id
        if lesson.student is not None:
            return _is_student(lesson, caller)
        return lesson.status == "open" and lesson.starts_at > self._clock()

    @staticmethod
    def _me(caller: Caller) -> Person:
        return Person(id=caller.id, name=caller.name)
