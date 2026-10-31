import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from tech_support_agent.graph import create_lessons_agent
from tests.conftest import as_student, as_teacher, call, scripted_model
from tests.fake_lessons_api import FakeLessonsApi

KYIV = {"timezone": "Europe/Kyiv"}


def agent_with(*messages: AIMessage):
    return create_lessons_agent(
        scripted_model(*messages, AIMessage(content="All set")), InMemorySaver()
    )


async def pause(agent, config, text: str = "Do it"):
    result = await agent.ainvoke(
        {"messages": [HumanMessage(text)]}, config, context=KYIV
    )
    assert "__interrupt__" in result, result["messages"][-1]
    return result["__interrupt__"][0].value


async def test_booking_pauses_with_a_described_request(
    lessons_api: FakeLessonsApi,
) -> None:
    agent = agent_with(call("book_lesson", lesson_id="math-tue"))

    request = await pause(agent, as_student())

    assert request["action_requests"] == [
        {
            "name": "book_lesson",
            "args": {"lesson_id": "math-tue"},
            "description": "Book Math with Tom Teacher on Tuesday, 03 November 2026, "
            "14:00–15:00 (Europe/Kyiv), 60 min.",
        }
    ]
    assert request["review_configs"] == [
        {"action_name": "book_lesson", "allowed_decisions": ["approve", "reject"]}
    ]
    assert lessons_api.writes == []


async def test_approve_executes_the_booking_once(lessons_api: FakeLessonsApi) -> None:
    agent = agent_with(call("book_lesson", lesson_id="math-tue"))
    config = as_student()
    await pause(agent, config)

    result = await agent.ainvoke(
        Command(resume={"decisions": [{"type": "approve"}]}), config, context=KYIV
    )

    assert [request.url.path for request in lessons_api.writes] == [
        "/lessons/math-tue/book"
    ]
    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert tool_message.status != "error"
    assert json.loads(tool_message.content)["outcome"] == "booked"
    assert result["messages"][-1].content == "All set"


async def test_reject_executes_nothing_and_tells_the_model(
    lessons_api: FakeLessonsApi,
) -> None:
    agent = agent_with(call("book_lesson", lesson_id="math-tue"))
    config = as_student()
    await pause(agent, config)

    result = await agent.ainvoke(
        Command(resume={"decisions": [{"type": "reject", "message": "Wrong day"}]}),
        config,
        context=KYIV,
    )

    assert lessons_api.writes == []
    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert tool_message.status == "error"
    assert "Wrong day" in tool_message.content
    assert lessons_api.lessons["math-tue"]["status"] == "open"


async def test_new_lesson_is_described_in_local_time(
    lessons_api: FakeLessonsApi,
) -> None:
    agent = agent_with(
        call(
            "create_lesson",
            subject="English",
            starts_at="2026-11-06T10:00",
            duration_minutes=90,
        )
    )
    config = as_teacher()

    request = await pause(agent, config)

    assert request["action_requests"][0]["description"] == (
        "Open a new 90-minute lesson: English on Friday, 06 November 2026, "
        "10:00–11:30 (Europe/Kyiv)."
    )
    await agent.ainvoke(
        Command(resume={"decisions": [{"type": "approve"}]}), config, context=KYIV
    )
    body = json.loads(lessons_api.writes[0].content)
    assert body == {
        "subject": "English",
        "starts_at": "2026-11-06T10:00:00+02:00",
        "duration_minutes": 90,
    }


async def test_cancellation_wording_depends_on_the_role(
    lessons_api: FakeLessonsApi,
) -> None:
    student = await pause(
        agent_with(call("cancel_lesson", lesson_id="art-tue")), as_student("s")
    )
    teacher = await pause(
        agent_with(call("cancel_lesson", lesson_id="art-tue")), as_teacher("t")
    )

    assert student["action_requests"][0]["description"] == (
        "Cancel your booking of Art with Tom Teacher on Tuesday, 03 November 2026, "
        "17:00–18:00 (Europe/Kyiv). The lesson reopens for other students."
    )
    assert teacher["action_requests"][0]["description"] == (
        "Cancel your lesson Art on Tuesday, 03 November 2026, 17:00–18:00 "
        "(Europe/Kyiv). It is booked by Alice, who will see it as cancelled."
    )


async def test_unknown_lessons_are_still_gated(lessons_api: FakeLessonsApi) -> None:
    request = await pause(
        agent_with(call("cancel_lesson", lesson_id="ghost")), as_student()
    )

    assert request["action_requests"][0]["description"] == (
        "Cancel lesson ghost. Its details could not be loaded."
    )


async def test_description_falls_back_to_lessons_shown_earlier(
    lessons_api: FakeLessonsApi,
) -> None:
    agent = agent_with(
        call("find_available_lessons", date_from="2026-11-03"),
        call("book_lesson", lesson_id="math-tue"),
    )
    config = as_student()
    lessons_api.hidden.add("math-tue")
    request = await pause(agent, config)

    assert request["action_requests"][0]["description"].startswith(
        "Book Math with Tom Teacher on Tuesday, 03 November 2026"
    )


async def test_tokens_stay_out_of_the_interrupt_and_state(
    lessons_api: FakeLessonsApi,
) -> None:
    agent = agent_with(call("book_lesson", lesson_id="math-tue"))
    config = as_student()

    request = await pause(agent, config)
    state = await agent.aget_state(config)

    assert "student-token" not in json.dumps(request)
    assert "student-token" not in str(state.values)
