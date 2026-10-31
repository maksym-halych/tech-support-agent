"""Offline models, pinned settings and a stand-in for Agent Server's user."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import RunnableConfig

from tech_support_agent.config import Settings


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the chat model a placeholder key and keep tracing off.

    The OpenAI client refuses to construct without a key, which ``build_agent``
    does; no test calls the API, so the key never leaves the process.
    """

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-placeholder")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    for name in ("AGENT_MODEL", "OPENAI_BASE_URL", "CLERK_ISSUER", "LESSONS_API_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def settings(monkeypatch: pytest.MonkeyPatch, isolate_environment: None) -> Settings:
    """Pin the settings ``build_agent`` reads, ignoring the developer's .env."""

    pinned = Settings(_env_file=None)
    monkeypatch.setattr("tech_support_agent.graph.get_settings", lambda: pinned)
    return pinned


class FakeToolCallingModel(GenericFakeChatModel):
    """Replays scripted messages, accepts tools and records every prompt.

    ``GenericFakeChatModel`` refuses ``bind_tools``; the agent needs it, and the
    scripted messages already carry whatever tool calls a test wants.
    """

    prompts: list[list[BaseMessage]] = []

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> BaseChatModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.prompts.append(list(messages))
        return super()._generate(messages, stop, run_manager, **kwargs)


def scripted_model(*messages: AIMessage) -> FakeToolCallingModel:
    """Build a fake model that answers each model call with the next message."""

    return FakeToolCallingModel(messages=iter(messages), prompts=[])


def tool_call_message(name: str, count: int = 1, **args: Any) -> AIMessage:
    """Build an AI message calling ``name`` ``count`` times in parallel."""

    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call-{i}", "type": "tool_call"}
            for i in range(count)
        ],
    )


@dataclass
class ServerUser:
    """Mimics the user object Agent Server puts in ``langgraph_auth_user``."""

    identity: str
    display_name: str
    data: dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]


def as_user(
    identity: str = "user_alice",
    name: str = "Alice",
    role: str = "student",
    token: str = "clerk-session-token",
) -> RunnableConfig:
    """Run config that makes the agent act as a signed-in Clerk user."""

    user = ServerUser(identity, name, {"role": role, "access_token": token})
    return {"configurable": {"langgraph_auth_user": user}}
