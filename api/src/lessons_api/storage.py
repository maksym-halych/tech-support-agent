"""SQLite persistence for users and lessons."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from lessons_api.models import Caller, Lesson, LessonStatus, Person, Teacher

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('student', 'teacher')),
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lessons (
    id TEXT PRIMARY KEY,
    teacher_id TEXT NOT NULL REFERENCES users (id),
    subject TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes BETWEEN 15 AND 240),
    status TEXT NOT NULL CHECK (status IN ('open', 'booked', 'cancelled')),
    student_id TEXT REFERENCES users (id),
    created_at TEXT NOT NULL,
    cancelled_at TEXT
);
CREATE INDEX IF NOT EXISTS lessons_teacher ON lessons (teacher_id, starts_at);
CREATE INDEX IF NOT EXISTS lessons_student ON lessons (student_id, starts_at);
CREATE INDEX IF NOT EXISTS lessons_status ON lessons (status, starts_at);
"""

LESSON_SELECT = """
SELECT l.id, l.subject, l.starts_at, l.duration_minutes, l.status,
       l.created_at, l.cancelled_at,
       t.id, t.name, s.id, s.name
FROM lessons l
JOIN users t ON t.id = l.teacher_id
LEFT JOIN users s ON s.id = l.student_id
"""


def to_db_time(value: datetime) -> str:
    """
    Serialize a datetime as UTC ISO 8601, so stored values sort chronologically.

    Args:
        value: A timezone-aware datetime.

    Returns:
        The UTC timestamp with second precision.
    """

    return value.astimezone(UTC).isoformat(timespec="seconds")


def _from_db_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _lesson(row: sqlite3.Row) -> Lesson:
    return Lesson(
        id=row[0],
        subject=row[1],
        starts_at=datetime.fromisoformat(row[2]),
        duration_minutes=row[3],
        status=row[4],
        created_at=datetime.fromisoformat(row[5]),
        cancelled_at=_from_db_time(row[6]),
        teacher=Person(id=row[7], name=row[8]),
        student=Person(id=row[9], name=row[10]) if row[9] is not None else None,
    )


class Store:
    """Thread-safe access to the users and lessons tables."""

    def __init__(self, database: Path | str = ":memory:") -> None:
        """
        Open the database and create the schema if needed.

        Args:
            database: SQLite file path, or ``:memory:`` for an isolated store.
        """

        self._connection = sqlite3.connect(
            str(database), check_same_thread=False, isolation_level=None
        )
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(SCHEMA)
        self._lock = RLock()
        self._depth = 0

    def close(self) -> None:
        """Close the underlying connection."""

        self._connection.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """
        Run several reads and writes atomically and exclusively.

        Yields:
            Nothing; nested calls join the outer transaction.
        """

        with self._lock:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            self._connection.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")
            finally:
                self._depth = 0

    def upsert_user(self, caller: Caller, seen_at: datetime) -> None:
        """
        Record the caller's latest name, email and role.

        Args:
            caller: The verified caller.
            seen_at: When the caller made the request.
        """

        with self._lock:
            self._connection.execute(
                """
                INSERT INTO users (id, name, email, role, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    name = excluded.name,
                    email = excluded.email,
                    role = excluded.role,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    caller.id,
                    caller.name,
                    caller.email,
                    caller.role,
                    to_db_time(seen_at),
                ),
            )

    def list_teachers(self) -> list[Teacher]:
        """
        Return users whose latest role is teacher, ordered by name.

        Returns:
            The known teachers.
        """

        with self._lock:
            rows = self._connection.execute(
                "SELECT id, name FROM users WHERE role = 'teacher' ORDER BY name, id"
            ).fetchall()
        return [Teacher(id=row[0], name=row[1]) for row in rows]

    def get_lesson(self, lesson_id: str) -> Lesson | None:
        """
        Return one lesson by ID.

        Args:
            lesson_id: The lesson ID.

        Returns:
            The lesson, or None if it does not exist.
        """

        with self._lock:
            row = self._connection.execute(
                f"{LESSON_SELECT} WHERE l.id = ?", (lesson_id,)
            ).fetchone()
        return _lesson(row) if row else None

    def lessons_for_teacher(
        self, teacher_id: str, starts_from: datetime | None = None
    ) -> list[Lesson]:
        """
        Return a teacher's lessons in any status, ordered by start time.

        Args:
            teacher_id: The teacher's user ID.
            starts_from: Only lessons starting at or after this time, if given.

        Returns:
            The teacher's lessons.
        """

        return self._query(
            "l.teacher_id = ?", teacher_id, starts_from=starts_from, starts_before=None
        )

    def lessons_for_student(
        self, student_id: str, starts_from: datetime | None = None
    ) -> list[Lesson]:
        """
        Return lessons a student has booked, including ones the teacher cancelled.

        Args:
            student_id: The student's user ID.
            starts_from: Only lessons starting at or after this time, if given.

        Returns:
            The student's lessons, ordered by start time.
        """

        return self._query(
            "l.student_id = ?", student_id, starts_from=starts_from, starts_before=None
        )

    def open_lessons(
        self,
        starts_from: datetime,
        starts_before: datetime | None,
        teacher_id: str | None,
        limit: int,
    ) -> list[Lesson]:
        """
        Return open lessons in a time window, ordered by start time.

        Args:
            starts_from: Earliest start time, inclusive.
            starts_before: Latest start time, exclusive; None for no limit.
            teacher_id: Only this teacher's lessons, if given.
            limit: Maximum number of lessons.

        Returns:
            The matching open lessons.
        """

        condition, params = "l.status = 'open'", []
        if teacher_id is not None:
            condition += " AND l.teacher_id = ?"
            params.append(teacher_id)
        return self._query(
            condition,
            *params,
            starts_from=starts_from,
            starts_before=starts_before,
            limit=limit,
        )

    def insert_lesson(self, lesson: Lesson) -> None:
        """
        Store a new lesson.

        Args:
            lesson: The lesson; its teacher must already be a stored user.
        """

        with self._lock:
            self._connection.execute(
                "INSERT INTO lessons VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    lesson.id,
                    lesson.teacher.id,
                    lesson.subject,
                    to_db_time(lesson.starts_at),
                    lesson.duration_minutes,
                    lesson.status,
                    lesson.student.id if lesson.student else None,
                    to_db_time(lesson.created_at),
                    None,
                ),
            )

    def update_lesson(
        self,
        lesson_id: str,
        status: LessonStatus,
        student_id: str | None,
        cancelled_at: datetime | None = None,
    ) -> None:
        """
        Change a lesson's status and booking.

        Args:
            lesson_id: The lesson ID.
            status: The new status.
            student_id: The booked student, or None for an unbooked lesson.
            cancelled_at: When the teacher cancelled the lesson, if they did.
        """

        with self._lock:
            self._connection.execute(
                "UPDATE lessons SET status = ?, student_id = ?, cancelled_at = ? WHERE id = ?",
                (
                    status,
                    student_id,
                    to_db_time(cancelled_at) if cancelled_at else None,
                    lesson_id,
                ),
            )

    def _query(
        self,
        condition: str,
        *params: str,
        starts_from: datetime | None,
        starts_before: datetime | None,
        limit: int = -1,
    ) -> list[Lesson]:
        values: list[str | int] = list(params)
        if starts_from is not None:
            condition += " AND l.starts_at >= ?"
            values.append(to_db_time(starts_from))
        if starts_before is not None:
            condition += " AND l.starts_at < ?"
            values.append(to_db_time(starts_before))
        values.append(limit)
        with self._lock:
            rows = self._connection.execute(
                f"{LESSON_SELECT} WHERE {condition} ORDER BY l.starts_at, l.id LIMIT ?",
                values,
            ).fetchall()
        return [_lesson(row) for row in rows]
