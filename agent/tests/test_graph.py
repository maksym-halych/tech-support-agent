import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.pregel import Pregel

from tech_support_agent.config import Settings
from tech_support_agent.graph import (
    build_agent,
    create_chat_model,
    create_tech_support_agent,
)
from tests.conftest import scripted_model, tool_call_message


def test_build_agent_compiles() -> None:
    assert isinstance(build_agent(), Pregel)


def test_build_agent_wires_a_tool_node() -> None:
    assert "tools" in build_agent().nodes


def test_chat_model_targets_the_base_url_over_chat_completions() -> None:
    model = create_chat_model(Settings(_env_file=None))

    assert model.model_name == "openai/gpt-6.1-sol"
    assert model.openai_api_base == "https://openrouter.ai/api/v1"
    assert model.use_responses_api is False


def test_agent_runs_a_tool_and_answers() -> None:
    agent = create_tech_support_agent(
        scripted_model(
            tool_call_message("get_current_datetime", 1),
            AIMessage(content="Your certificate expired yesterday."),
        )
    )

    result = agent.invoke({"messages": [HumanMessage("Is my cert expired?")]})

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].status != "error"
    assert result["messages"][-1].content == "Your certificate expired yesterday."


def test_chat_model_needs_an_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")

    with pytest.raises(ValueError, match="OPENAI_API_KEY is not set"):
        create_chat_model(Settings(_env_file=None))
