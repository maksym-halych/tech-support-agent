from collections.abc import Sequence
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from tech_support_agent.config import Settings


@pytest.fixture(autouse=True)
def openai_api_key(monkeypatch) -> None:
    """Give the chat model a placeholder API key.

    The OpenAI client refuses to construct without one, which ``build_agent``
    does; no test calls the API, so the key never leaves the process.
    """

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-placeholder")


@pytest.fixture(autouse=True)
def settings(monkeypatch, openai_api_key) -> Settings:
    """Pin the settings ``build_agent`` reads.

    The developer's .env is ignored, so a local model id or key cannot change
    what the tests see. Tests adjust the returned object.
    """

    pinned = Settings(_env_file=None)
    monkeypatch.setattr("tech_support_agent.graph.get_settings", lambda: pinned)
    return pinned


class FakeToolCallingModel(GenericFakeChatModel):
    """A fake chat model that replays scripted messages and accepts tools.

    ``GenericFakeChatModel`` refuses ``bind_tools``; the agent needs it, and the
    scripted messages already carry whatever tool calls a test wants.
    """

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> BaseChatModel:
        return self


def scripted_model(*messages: AIMessage) -> FakeToolCallingModel:
    """Build a fake model that answers each model call with the next message."""

    return FakeToolCallingModel(messages=iter(messages))


def tool_call_message(name: str, count: int) -> AIMessage:
    """Build an AI message calling ``name`` ``count`` times in parallel."""

    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": {}, "id": f"call-{i}", "type": "tool_call"}
            for i in range(count)
        ],
    )
