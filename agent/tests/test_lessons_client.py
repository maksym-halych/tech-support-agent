from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from tech_support_agent.context import SignedInUser
from tech_support_agent.lessons.client import (
    SERVER_ERROR,
    SESSION_EXPIRED,
    UNAVAILABLE,
    LessonsApiError,
    LessonsClient,
)
from tests.fake_lessons_api import FakeLessonsApi, lesson

USER = SignedInUser(
    id="user_alice", name="Alice", role="student", token=SecretStr("secret-token")
)


def client(handler) -> LessonsClient:
    return LessonsClient("http://lessons.test", USER, httpx.MockTransport(handler))


async def test_requests_carry_the_users_token_and_map_to_routes() -> None:
    api = FakeLessonsApi()
    lessons = client(api.handle)
    start = datetime(2026, 11, 3, tzinfo=UTC)
    end = datetime(2026, 11, 4, tzinfo=UTC)

    await lessons.my_lessons(include_past=True)
    await lessons.teachers()
    await lessons.available_lessons(start, end, "user_tom")
    await lessons.get_lesson("math-tue")
    await lessons.book_lesson("math-tue")
    await lessons.cancel_lesson("math-tue")
    created = await lessons.create_lesson("English", start, 45)

    assert [(r.method, r.url.path) for r in api.requests] == [
        ("GET", "/lessons/mine"),
        ("GET", "/teachers"),
        ("GET", "/lessons/available"),
        ("GET", "/lessons/math-tue"),
        ("POST", "/lessons/math-tue/book"),
        ("POST", "/lessons/math-tue/cancel"),
        ("POST", "/lessons"),
    ]
    assert all(
        r.headers["Authorization"] == "Bearer secret-token" for r in api.requests
    )
    assert api.requests[0].url.params["include_past"] == "true"
    assert dict(api.requests[2].url.params) == {
        "starts_after": "2026-11-03T00:00:00+00:00",
        "starts_before": "2026-11-04T00:00:00+00:00",
        "teacher_id": "user_tom",
    }
    assert created.subject == "English"
    assert created.duration_minutes == 45


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, {"detail": "Invalid or missing session token"}, SESSION_EXPIRED),
        (
            403,
            {"detail": "Only students can book lessons"},
            "Only students can book lessons",
        ),
        (404, {"detail": "Lesson not found"}, "Lesson not found"),
        (
            409,
            {"detail": "Someone else has already booked this lesson"},
            "Someone else has already booked this lesson",
        ),
        (
            422,
            {"detail": [{"msg": "Value error, A lesson must start in the future"}]},
            "Value error, A lesson must start in the future",
        ),
        (500, {"detail": "Traceback with internals"}, SERVER_ERROR),
        (503, {"detail": "Cannot verify"}, SERVER_ERROR),
    ],
)
async def test_errors_become_plain_messages(
    status: int, body: dict, message: str
) -> None:
    lessons = client(lambda request: httpx.Response(status, json=body))

    with pytest.raises(LessonsApiError) as raised:
        await lessons.book_lesson("math-tue")

    assert raised.value.status == status
    assert raised.value.message == message


async def test_reads_retry_once_after_a_network_error() -> None:
    attempts: list[httpx.Request] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json=[lesson("math-tue")])

    lessons = await client(flaky).my_lessons()

    assert len(attempts) == 2
    assert [item.id for item in lessons] == ["math-tue"]


async def test_reads_give_up_after_the_retry() -> None:
    attempts: list[httpx.Request] = []

    def down(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LessonsApiError) as raised:
        await client(down).teachers()

    assert len(attempts) == 2
    assert (raised.value.status, raised.value.message) == (0, UNAVAILABLE)


async def test_writes_are_never_retried() -> None:
    attempts: list[httpx.Request] = []

    def timeout(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LessonsApiError) as raised:
        await client(timeout).book_lesson("math-tue")

    assert len(attempts) == 1
    assert raised.value.message == UNAVAILABLE


async def test_unexpected_responses_are_server_errors() -> None:
    lessons = client(lambda request: httpx.Response(200, json={"unexpected": True}))

    with pytest.raises(LessonsApiError) as raised:
        await lessons.get_lesson("math-tue")

    assert raised.value.message == SERVER_ERROR


def test_the_token_never_shows_in_reprs() -> None:
    assert "secret-token" not in repr(USER)
    assert "secret-token" not in USER.model_dump_json()
