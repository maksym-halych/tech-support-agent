import json
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.pregel import Pregel
from pydantic import ValidationError

from tech_support_agent.config import Settings
from tech_support_agent.context import AgentContext
from tech_support_agent.graph import (
    build_agent,
    create_chat_model,
    create_lessons_agent,
)
from tests.conftest import as_user, scripted_model, tool_call_message


def test_build_agent_compiles_with_a_tool_node() -> None:
    agent = build_agent()
    assert isinstance(agent, Pregel)
    assert "tools" in agent.nodes


def test_chat_model_targets_openai_over_the_responses_api() -> None:
    model = create_chat_model(Settings(_env_file=None))

    assert model.model_name == "gpt-6.1-sol"
    assert model.openai_api_base == "https://api.openai.com/v1"
    assert model.use_responses_api is True


def test_chat_model_targets_other_providers_over_chat_completions() -> None:
    settings = Settings(_env_file=None, openai_base_url="https://openrouter.ai/api/v1")

    model = create_chat_model(settings)

    assert model.openai_api_base == "https://openrouter.ai/api/v1"
    assert model.use_responses_api is False


def test_chat_model_needs_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")

    with pytest.raises(ValueError, match="OPENAI_API_KEY is not set"):
        create_chat_model(Settings(_env_file=None))


def _system_prompt(model) -> str:
    first = model.prompts[0][0]
    assert isinstance(first, SystemMessage)
    return first.content


def test_prompt_names_the_user_role_and_local_time() -> None:
    model = scripted_model(AIMessage(content="Hi"))
    agent = create_lessons_agent(model)

    agent.invoke(
        {"messages": [HumanMessage("Hi")]},
        as_user(name="Tom Teacher", role="teacher"),
        context={"timezone": "America/New_York"},
    )

    prompt = _system_prompt(model)
    assert "- Name: Tom Teacher" in prompt
    assert "- Role: teacher" in prompt
    assert "- Timezone: America/New_York" in prompt
    assert "clerk-session-token" not in prompt


def test_prompt_defaults_to_kyiv_time() -> None:
    model = scripted_model(AIMessage(content="Hi"))

    create_lessons_agent(model).invoke({"messages": [HumanMessage("Hi")]}, as_user())

    assert "- Timezone: Europe/Kyiv" in _system_prompt(model)


def test_prompt_without_a_signed_in_user_refuses_lesson_work() -> None:
    model = scripted_model(AIMessage(content="Please sign in."))

    create_lessons_agent(model).invoke({"messages": [HumanMessage("Book")]})

    assert "nobody is signed in" in _system_prompt(model)


def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Unknown timezone"):
        AgentContext(timezone="Mars/Olympus")


def test_datetime_tool_uses_the_run_timezone() -> None:
    model = scripted_model(
        tool_call_message("get_current_datetime"), AIMessage(content="Done")
    )

    result = create_lessons_agent(model).invoke(
        {"messages": [HumanMessage("What time is it?")]},
        as_user(),
        context={"timezone": "Asia/Tokyo"},
    )

    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    payload = json.loads(tool_message.content)
    assert payload["timezone"] == "Asia/Tokyo"
    assert payload["local"].endswith("+09:00")
    assert ZoneInfo(payload["timezone"])
