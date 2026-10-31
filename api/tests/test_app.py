from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB


def test_requests_without_a_known_token_are_rejected(client: TestClient) -> None:
    assert client.get("/teachers").status_code in {401, 403}
    unknown = {"Authorization": "Bearer someone"}
    assert client.get("/teachers", headers=unknown).status_code == 401


def test_lists_teachers(client: TestClient) -> None:
    response = client.get("/teachers", headers=ALICE)
    assert response.status_code == 200
    assert [teacher["id"] for teacher in response.json()] == ["iryna", "oleh"]


def test_slots_are_future_weekday_and_filterable(client: TestClient) -> None:
    saturday = client.get("/slots", params={"date": "2026-10-31"}, headers=ALICE)
    assert saturday.json() == []
    tuesday = client.get(
        "/slots",
        params={"date": "2026-11-03", "teacher_id": "iryna"},
        headers=ALICE,
    )
    assert [slot["id"] for slot in tuesday.json()] == [
        "iryna:2026-11-03:10",
        "iryna:2026-11-03:14",
        "iryna:2026-11-03:16",
    ]


def test_booked_slot_disappears_from_search(client: TestClient) -> None:
    headers = {**ALICE, "Idempotency-Key": "k1"}
    booked = client.post(
        "/me/bookings", headers=headers, json={"slot_id": "oleh:2026-11-03:10"}
    )
    assert booked.status_code == 201
    slots = client.get("/slots", params={"date": "2026-11-03"}, headers=BOB).json()
    assert "oleh:2026-11-03:10" not in {slot["id"] for slot in slots}


def test_unknown_or_past_slot_is_404(client: TestClient) -> None:
    headers = {**ALICE, "Idempotency-Key": "k1"}
    for slot_id in ("nonsense", "iryna:2026-10-30:10", "iryna:2026-11-03:11"):
        response = client.post(
            "/me/bookings", headers=headers, json={"slot_id": slot_id}
        )
        assert response.status_code == 404


def test_idempotency_conflict_and_user_isolation(client: TestClient) -> None:
    headers = {**ALICE, "Idempotency-Key": "once"}
    body = {"slot_id": "iryna:2026-11-03:14"}
    first = client.post("/me/bookings", headers=headers, json=body)
    repeat = client.post("/me/bookings", headers=headers, json=body)
    assert first.status_code == repeat.status_code == 201
    assert first.json()["id"] == repeat.json()["id"]
    conflict = client.post(
        "/me/bookings", headers={**headers, "Idempotency-Key": "twice"}, json=body
    )
    assert conflict.status_code == 409
    different = client.post(
        "/me/bookings", headers=headers, json={"slot_id": "oleh:2026-11-03:14"}
    )
    assert different.status_code == 409
    assert client.get("/me/bookings", headers=ALICE).json() == [first.json()]
    assert client.get("/me/bookings", headers=BOB).json() == []
    booking_id = first.json()["id"]
    assert client.get(f"/me/bookings/{booking_id}", headers=ALICE).status_code == 200
    assert client.get(f"/me/bookings/{booking_id}", headers=BOB).status_code == 404
