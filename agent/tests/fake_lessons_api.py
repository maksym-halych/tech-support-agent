"""An in-memory stand-in for the lessons API that records every request."""

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

TOM = {"id": "user_tom", "name": "Tom Teacher"}
ALICE = {"id": "user_alice", "name": "Alice"}


def lesson(
    lesson_id: str,
    subject: str = "Math",
    starts_at: str = "2026-11-03T12:00:00Z",
    ends_at: str = "2026-11-03T13:00:00Z",
    status: str = "open",
    student: dict[str, str] | None = None,
) -> dict[str, Any]:
    """A lesson as the API serializes it."""

    return {
        "id": lesson_id,
        "subject": subject,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "duration_minutes": 60,
        "status": status,
        "teacher": TOM,
        "student": student,
        "created_at": "2026-10-30T09:00:00Z",
        "cancelled_at": None,
    }


@dataclass
class FakeLessonsApi:
    """Answers the lessons API's routes from a dict of lessons."""

    lessons: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            "math-tue": lesson("math-tue"),
            "art-tue": lesson(
                "art-tue",
                "Art",
                "2026-11-03T15:00:00Z",
                "2026-11-03T16:00:00Z",
                "booked",
                ALICE,
            ),
        }
    )
    requests: list[httpx.Request] = field(default_factory=list)
    # Status code to return for every request, to simulate failures.
    fail_with: int | None = None
    # Lessons GET /lessons/{id} reports as missing, though lists include them.
    hidden: set[str] = field(default_factory=set)

    @property
    def writes(self) -> list[httpx.Request]:
        return [request for request in self.requests if request.method == "POST"]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_with is not None:
            return httpx.Response(self.fail_with, json={"detail": "Simulated failure"})
        path = request.url.path
        parts = path.strip("/").split("/")
        if request.method == "GET" and path == "/teachers":
            return httpx.Response(200, json=[TOM])
        if request.method == "GET" and path == "/lessons/mine":
            return httpx.Response(200, json=list(self.lessons.values()))
        if request.method == "GET" and path == "/lessons/available":
            open_lessons = [
                item for item in self.lessons.values() if item["status"] == "open"
            ]
            return httpx.Response(200, json=open_lessons)
        if request.method == "POST" and path == "/lessons":
            body = json.loads(request.content)
            created = lesson(
                "new-1", body["subject"], body["starts_at"], body["starts_at"]
            )
            created["duration_minutes"] = body["duration_minutes"]
            self.lessons["new-1"] = created
            return httpx.Response(201, json=created)
        if parts[0] == "lessons" and len(parts) >= 2:
            found = self.lessons.get(parts[1])
            hidden = request.method == "GET" and parts[1] in self.hidden
            if found is None or hidden:
                return httpx.Response(404, json={"detail": "Lesson not found"})
            if request.method == "GET" and len(parts) == 2:
                return httpx.Response(200, json=found)
            if request.method == "POST" and parts[2:] == ["book"]:
                if found["status"] != "open":
                    return httpx.Response(
                        409,
                        json={"detail": "Someone else has already booked this lesson"},
                    )
                found.update(status="booked", student=ALICE)
                return httpx.Response(200, json=found)
            if request.method == "POST" and parts[2:] == ["cancel"]:
                found.update(status="open", student=None)
                return httpx.Response(200, json=found)
        return httpx.Response(404, json={"detail": "Not Found"})
