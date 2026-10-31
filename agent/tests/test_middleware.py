from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tech_support_agent.graph import create_lessons_agent
from tech_support_agent.middleware import TOOL_CALL_LIMIT_MESSAGE
from tech_support_agent.prompts import MAX_TOOL_CALLS_PER_TURN
from tests.conftest import scripted_model, tool_call_message


def test_tool_calls_over_the_limit_are_refused_with_our_message() -> None:
    over = MAX_TOOL_CALLS_PER_TURN + 2
    agent = create_lessons_agent(
        scripted_model(
            tool_call_message("get_current_datetime", over),
            AIMessage(content="Done."),
        )
    )

    result = agent.invoke({"messages": [HumanMessage("What time is it?")]})

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    refused = [m for m in tool_messages if m.content == TOOL_CALL_LIMIT_MESSAGE]
    assert len(tool_messages) == over
    assert len(refused) == over - MAX_TOOL_CALLS_PER_TURN
    assert result["messages"][-1].content == "Done."
