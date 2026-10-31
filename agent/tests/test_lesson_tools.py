import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from tech_support_agent.graph import create_lessons_agent
from tech_support_agent.tools.registry import TOOLS, tool_names_for
from tests.conftest import as_student, as_teacher, call, scripted_model
from tests.fake_lessons_api import FakeLessonsApi

KYIV = {"timezone": "Europe/Kyiv"}


async def run(config, *messages: AIMessage, context=KYIV):
    model = scripted_model(*messages, AIMessage(content="Done"))
    agent = create_lessons_agent(model, InMemorySaver())
    result = await agent.ainvoke(
        {"messages": [HumanMessage("Help")]}, config, context=context
    )
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    return model, result, tool_messages


async def test_students_see_student_tools(lessons_api: FakeLessonsApi) -> None:
    model, _, _ = await run(as_student())

    assert model.tool_names[0] == [
        "book_lesson",
        "cancel_lesson",
        "find_available_lessons",
        "get_current_datetime",
        "list_my_lessons",
        "list_teachers",
    ]


async def test_teachers_see_teacher_tools(lessons_api: FakeLessonsApi) -> None:
    model, _, _ = await run(as_teacher())

    assert model.tool_names[0] == [
        "cancel_lesson",
        "create_lesson",
        "get_current_datetime",
        "list_my_lessons",
    ]


async def test_nobody_signed_in_sees_only_the_clock() -> None:
    model = scripted_model(AIMessage(content="Please sign in"))
    agent = create_lessons_agent(model)

    await agent.ainvoke({"messages": [HumanMessage("Book")]})

    assert model.tool_names[0] == ["get_current_datetime"]
    assert tool_names_for(None) == {"get_current_datetime"}


def test_every_tool_belongs_to_some_role() -> None:
    names = {tool.name for tool in TOOLS}
    assert names == tool_names_for("student") | tool_names_for("teacher")


async def test_find_available_lessons_searches_local_days(
    lessons_api: FakeLessonsApi,
) -> None:
    _, _, tools = await run(
        as_student(),
        call(
            "find_available_lessons",
            date_from="2026-11-03",
            date_to="2026-11-04",
            teacher_id="user_tom",
        ),
    )

    params = dict(lessons_api.requests[0].url.params)
    assert params == {
        "starts_after": "2026-11-03T00:00:00+02:00",
        "starts_before": "2026-11-05T00:00:00+02:00",
        "teacher_id": "user_tom",
    }
    payload = json.loads(tools[0].content)
    assert payload["timezone"] == "Europe/Kyiv"
    assert payload["lessons"] == [
        {
            "id": "math-tue",
            "subject": "Math",
            "status": "open",
            "teacher": "Tom Teacher",
            "day": "Tuesday, 03 November 2026",
            "time": "14:00–15:00",
            "duration_minutes": 60,
            "starts_at_local": "2026-11-03T14:00+02:00",
            "starts_at_utc": "2026-11-03T12:00+00:00",
        }
    ]


async def test_list_my_lessons_uses_the_run_timezone(
    lessons_api: FakeLessonsApi,
) -> None:
    _, _, tools = await run(
        as_student(),
        call("list_my_lessons", include_past=True),
        context={"timezone": "America/New_York"},
    )

    assert lessons_api.requests[0].url.params["include_past"] == "true"
    payload = json.loads(tools[0].content)
    assert payload["lessons"][1]["time"] == "10:00–11:00"
    assert payload["lessons"][1]["student"] == "Alice"


async def test_list_teachers(lessons_api: FakeLessonsApi) -> None:
    _, _, tools = await run(as_student(), call("list_teachers"))

    assert json.loads(tools[0].content) == {
        "teachers": [{"id": "user_tom", "name": "Tom Teacher"}]
    }


async def test_tool_output_never_contains_the_token(
    lessons_api: FakeLessonsApi,
) -> None:
    _, result, _ = await run(as_student(), call("list_my_lessons"))

    assert lessons_api.requests[0].headers["Authorization"] == "Bearer student-token"
    for message in result["messages"]:
        assert "student-token" not in str(message.content)


async def test_api_errors_become_tool_errors(lessons_api: FakeLessonsApi) -> None:
    lessons_api.fail_with = 500

    _, result, tools = await run(as_student(), call("list_my_lessons"))

    assert tools[0].status == "error"
    assert "internal problem" in tools[0].content
    assert result["messages"][-1].content == "Done"


async def test_wrong_role_tool_calls_fail_without_asking_or_calling_the_api(
    lessons_api: FakeLessonsApi,
) -> None:
    _, result, tools = await run(
        as_student(),
        call("create_lesson", subject="Math", starts_at="2026-11-06T10:00"),
    )

    assert "__interrupt__" not in result
    assert tools[0].status == "error"
    assert "only available to teachers" in tools[0].content
    assert lessons_api.requests == []
