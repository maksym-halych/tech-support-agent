from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    NOW,
    OTHER_STUDENT,
    OTHER_TEACHER,
    STUDENT,
    TEACHER,
    Clock,
    auth,
)

TOMORROW_10 = (NOW + timedelta(days=1)).replace(hour=10).isoformat()


def create(
    client: TestClient,
    starts_at: str = TOMORROW_10,
    headers: dict[str, str] = TEACHER,
    **body: Any,
) -> dict[str, Any]:
    response = client.post(
        "/lessons",
        headers=headers,
        json={"subject": "Math", "starts_at": starts_at, **body},
    )
    assert response.status_code == 201, response.text
    return response.json()


def at(hours: float) -> str:
    return (NOW + timedelta(hours=hours)).isoformat()


def book(client: TestClient, lesson_id: str, headers: dict[str, str] = STUDENT) -> Any:
    return client.post(f"/lessons/{lesson_id}/book", headers=headers)


def cancel(client: TestClient, lesson_id: str, headers: dict[str, str]) -> Any:
    return client.post(f"/lessons/{lesson_id}/cancel", headers=headers)


def ids(response: Any) -> list[str]:
    assert response.status_code == 200, response.text
    return [lesson["id"] for lesson in response.json()]


# --- creating -------------------------------------------------------------


def test_teacher_creates_an_open_lesson(client: TestClient) -> None:
    lesson = create(client, "2026-11-03T12:00:00+02:00", duration_minutes=45)
    assert lesson["status"] == "open"
    assert lesson["subject"] == "Math"
    assert lesson["teacher"] == {"id": "tom", "name": "Tom Teacher"}
    assert lesson["student"] is None
    assert lesson["starts_at"] == "2026-11-03T10:00:00Z"
    assert lesson["ends_at"] == "2026-11-03T10:45:00Z"
    assert lesson["cancelled_at"] is None


def test_students_cannot_create_lessons(client: TestClient) -> None:
    response = client.post(
        "/lessons", headers=STUDENT, json={"subject": "Math", "starts_at": at(24)}
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"subject": "Math", "starts_at": at(-1)},
        {"subject": "Math", "starts_at": "2026-11-03T10:00:00"},
        {"subject": "Math", "starts_at": at(24), "duration_minutes": 10},
        {"subject": "Math", "starts_at": at(24), "duration_minutes": 241},
        {"subject": "   ", "starts_at": at(24)},
        {"subject": "x" * 101, "starts_at": at(24)},
        {"subject": "Math", "starts_at": at(24), "teacher_id": "tina"},
    ],
    ids=["past", "naive", "too-short", "too-long", "blank", "long-subject", "extra"],
)
def test_invalid_lessons_are_rejected(client: TestClient, body: dict[str, Any]) -> None:
    assert client.post("/lessons", headers=TEACHER, json=body).status_code == 422


def test_overlapping_lessons_conflict(client: TestClient) -> None:
    create(client, at(24), duration_minutes=60)
    overlapping = client.post(
        "/lessons",
        headers=TEACHER,
        json={"subject": "Art", "starts_at": at(24.5)},
    )
    assert overlapping.status_code == 409
    assert "Math" in overlapping.json()["detail"]
    create(client, at(25))
    create(client, at(23), duration_minutes=60)
    create(client, at(24.5), headers=OTHER_TEACHER)


def test_cancelled_lessons_free_their_time(client: TestClient) -> None:
    lesson = create(client, at(24))
    assert cancel(client, lesson["id"], TEACHER).status_code == 200
    create(client, at(24))


# --- finding and booking --------------------------------------------------


def test_students_find_open_future_lessons(client: TestClient, clock: Clock) -> None:
    soon = create(client, at(2))
    later = create(client, at(30))
    other = create(client, at(5), headers=OTHER_TEACHER)
    booked = create(client, at(50))
    assert book(client, booked["id"], OTHER_STUDENT).status_code == 200

    available = client.get("/lessons/available", headers=STUDENT)
    assert ids(available) == [soon["id"], other["id"], later["id"]]

    window = client.get(
        "/lessons/available",
        headers=STUDENT,
        params={"starts_after": at(3), "starts_before": at(31)},
    )
    assert ids(window) == [other["id"], later["id"]]

    by_teacher = client.get(
        "/lessons/available", headers=STUDENT, params={"teacher_id": "tom"}
    )
    assert ids(by_teacher) == [soon["id"], later["id"]]

    clock.now += timedelta(hours=3)
    assert ids(client.get("/lessons/available", headers=STUDENT)) == [
        other["id"],
        later["id"],
    ]


def test_teachers_cannot_search_available_lessons(client: TestClient) -> None:
    assert client.get("/lessons/available", headers=TEACHER).status_code == 403


def test_naive_search_window_is_rejected(client: TestClient) -> None:
    response = client.get(
        "/lessons/available",
        headers=STUDENT,
        params={"starts_after": "2026-11-03T10:00:00"},
    )
    assert response.status_code == 422


def test_student_books_an_open_lesson(client: TestClient) -> None:
    lesson = create(client)
    response = book(client, lesson["id"])
    assert response.status_code == 200
    booked = response.json()
    assert booked["status"] == "booked"
    assert booked["student"] == {"id": "alice", "name": "Alice"}
    assert book(client, lesson["id"]).json() == booked
    assert ids(client.get("/lessons/available", headers=STUDENT)) == []


def test_booking_someone_elses_lesson_conflicts(client: TestClient) -> None:
    lesson = create(client)
    book(client, lesson["id"])
    response = book(client, lesson["id"], OTHER_STUDENT)
    assert response.status_code == 409
    assert "already booked" in response.json()["detail"]


def test_booking_rules(client: TestClient, clock: Clock) -> None:
    assert book(client, "missing").status_code == 404
    lesson = create(client, at(2))
    assert book(client, lesson["id"], TEACHER).status_code == 403

    cancelled = create(client, at(5))
    cancel(client, cancelled["id"], TEACHER)
    assert book(client, cancelled["id"]).status_code == 409

    overlapping = create(client, at(2.5), headers=OTHER_TEACHER)
    assert book(client, lesson["id"]).status_code == 200
    clash = book(client, overlapping["id"])
    assert clash.status_code == 409
    assert "You already have" in clash.json()["detail"]

    started = create(client, at(10))
    clock.now += timedelta(hours=10)
    assert book(client, started["id"], OTHER_STUDENT).status_code == 409


# --- listing and reading --------------------------------------------------


def test_each_user_sees_only_their_lessons(client: TestClient, clock: Clock) -> None:
    mine = create(client, at(24))
    open_one = create(client, at(26))
    cancelled = create(client, at(28))
    theirs = create(client, at(24), headers=OTHER_TEACHER)
    book(client, mine["id"])
    book(client, theirs["id"], OTHER_STUDENT)
    cancel(client, cancelled["id"], TEACHER)

    assert ids(client.get("/lessons/mine", headers=TEACHER)) == [
        mine["id"],
        open_one["id"],
        cancelled["id"],
    ]
    assert ids(client.get("/lessons/mine", headers=OTHER_TEACHER)) == [theirs["id"]]
    assert ids(client.get("/lessons/mine", headers=STUDENT)) == [mine["id"]]
    assert ids(client.get("/lessons/mine", headers=OTHER_STUDENT)) == [theirs["id"]]

    clock.now += timedelta(hours=24, minutes=30)
    assert mine["id"] in ids(client.get("/lessons/mine", headers=STUDENT))
    clock.now += timedelta(hours=1)
    assert ids(client.get("/lessons/mine", headers=STUDENT)) == []
    past = client.get("/lessons/mine", headers=STUDENT, params={"include_past": "true"})
    assert ids(past) == [mine["id"]]


def test_lesson_visibility(client: TestClient) -> None:
    lesson = create(client)
    url = f"/lessons/{lesson['id']}"
    assert client.get(url, headers=TEACHER).status_code == 200
    assert client.get(url, headers=OTHER_TEACHER).status_code == 404
    assert client.get(url, headers=STUDENT).status_code == 200
    book(client, lesson["id"])
    assert client.get(url, headers=STUDENT).json()["status"] == "booked"
    assert client.get(url, headers=OTHER_STUDENT).status_code == 404
    assert client.get("/lessons/missing", headers=STUDENT).status_code == 404


# --- cancelling -----------------------------------------------------------


def test_student_cancel_reopens_the_lesson(client: TestClient) -> None:
    lesson = create(client)
    book(client, lesson["id"])
    response = cancel(client, lesson["id"], STUDENT)
    assert response.status_code == 200
    assert response.json()["status"] == "open"
    assert response.json()["student"] is None
    assert ids(client.get("/lessons/mine", headers=STUDENT)) == []
    assert cancel(client, lesson["id"], STUDENT).status_code == 404
    assert book(client, lesson["id"], OTHER_STUDENT).status_code == 200


def test_students_cannot_cancel_other_bookings(client: TestClient) -> None:
    lesson = create(client)
    assert cancel(client, lesson["id"], STUDENT).status_code == 404
    book(client, lesson["id"], OTHER_STUDENT)
    assert cancel(client, lesson["id"], STUDENT).status_code == 404


def test_teacher_cancel_is_final_and_visible_to_the_student(
    client: TestClient,
) -> None:
    lesson = create(client)
    book(client, lesson["id"])
    assert cancel(client, lesson["id"], OTHER_TEACHER).status_code == 404
    response = cancel(client, lesson["id"], TEACHER)
    assert response.status_code == 200
    cancelled = response.json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancelled_at"] == NOW.isoformat().replace("+00:00", "Z")
    assert cancelled["student"] == {"id": "alice", "name": "Alice"}
    assert cancel(client, lesson["id"], TEACHER).json() == cancelled

    student_view = client.get("/lessons/mine", headers=STUDENT).json()
    assert [(item["id"], item["status"]) for item in student_view] == [
        (lesson["id"], "cancelled")
    ]
    again = cancel(client, lesson["id"], STUDENT)
    assert again.status_code == 409
    assert "teacher already cancelled" in again.json()["detail"]


def test_started_lessons_cannot_be_cancelled(client: TestClient, clock: Clock) -> None:
    lesson = create(client, at(1))
    book(client, lesson["id"])
    clock.now += timedelta(hours=1)
    assert cancel(client, lesson["id"], STUDENT).status_code == 409
    assert cancel(client, lesson["id"], TEACHER).status_code == 409


def test_concurrent_bookings_have_one_winner(client: TestClient) -> None:
    lesson = create(client)
    students = [auth(f"student{index}") for index in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = sorted(
            pool.map(
                lambda headers: book(client, lesson["id"], headers).status_code,
                students,
            )
        )
    assert statuses == [200] + [409] * 7
