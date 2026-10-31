"""Offline models, pinned settings and a stand-in for Agent Server's user."""

import os
import socket
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx
import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import RunnableConfig

from tech_support_agent.config import BugSettings, DemoSettings, Settings, get_settings
from tests.fake_lessons_api import FakeLessonsApi


def pytest_addoption(parser: pytest.Parser) -> None:
    for kind in ("server", "docker", "live"):
        parser.addoption(f"--run-{kind}", action="store_true", default=False)


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    for item in items:
        for kind in ("server", "docker", "live"):
            if item.get_closest_marker(kind) and not config.getoption(f"--run-{kind}"):
                item.add_marker(pytest.mark.skip(reason=f"requires --run-{kind}"))


@pytest.fixture(autouse=True)
def isolated_environment(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[None]:
    if request.node.get_closest_marker("live"):
        yield
        return
    for name in tuple(os.environ):
        if name.startswith(
            (
                "OPENAI_",
                "AGENT_",
                "BUG_",
                "CLERK_",
                "LESSONS_",
                "LANGSMITH_",
                "LANGCHAIN_",
                "TELEGRAM_",
                "GITHUB_",
                "GH_",
            )
        ):
            monkeypatch.delenv(name)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setitem(BugSettings.model_config, "env_file", None)
    monkeypatch.setitem(DemoSettings.model_config, "env_file", None)
    get_settings.cache_clear()
    connect = socket.socket.connect

    def local_only(sock: socket.socket, address: Any) -> None:
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1"):
            raise AssertionError(
                "External network access is forbidden in offline tests"
            )
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def openai_api_key(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    isolated_environment: None,
) -> None:
    """Give the chat model a placeholder API key.

    The OpenAI client refuses to construct without a key, which ``build_agent``
    does; no test calls the API, so the key never leaves the process.
    """

    if not request.node.get_closest_marker("live"):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-placeholder")


@pytest.fixture(autouse=True)
def settings(
    monkeypatch: pytest.MonkeyPatch,
    openai_api_key: None,
    request: pytest.FixtureRequest,
) -> Settings | None:
    """Pin the settings ``build_agent`` reads, ignoring the developer's .env.

    Tests adjust the returned object.
    """

    if request.node.get_closest_marker("live"):
        return None
    pinned = Settings(_env_file=None)
    for module in ("graph", "lessons.client", "auth"):
        monkeypatch.setattr(f"tech_support_agent.{module}.get_settings", lambda: pinned)
    return pinned


class FakeToolCallingModel(GenericFakeChatModel):
    """Replays scripted messages, accepts tools and records every prompt.

    ``GenericFakeChatModel`` refuses ``bind_tools``; the agent needs it, and the
    scripted messages already carry whatever tool calls a test wants.
    """

    prompts: list[list[BaseMessage]] = []
    tool_names: list[list[str]] = []

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> BaseChatModel:
        self.tool_names.append(sorted(getattr(tool, "name", "") for tool in tools))
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

    return FakeToolCallingModel(messages=iter(messages), prompts=[], tool_names=[])


def tool_call_message(name: str, count: int = 1, **args: Any) -> AIMessage:
    """Build an AI message calling ``name`` ``count`` times in parallel."""

    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call-{i}", "type": "tool_call"}
            for i in range(count)
        ],
    )


def call(name: str, **args: Any) -> AIMessage:
    """Build an AI message with one tool call, with a unique ID."""

    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call-{uuid4()}", "type": "tool_call"}
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


STUDENT = {
    "identity": "user_alice",
    "name": "Alice",
    "role": "student",
    "token": "student-token",
}
TEACHER = {
    "identity": "user_tom",
    "name": "Tom Teacher",
    "role": "teacher",
    "token": "teacher-token",
}


def as_student(thread_id: str = "t1") -> RunnableConfig:
    """Run config for the student Alice on a thread."""

    config = as_user(**STUDENT)
    config["configurable"]["thread_id"] = thread_id
    return config


def as_teacher(thread_id: str = "t1") -> RunnableConfig:
    """Run config for the teacher Tom on a thread."""

    config = as_user(**TEACHER)
    config["configurable"]["thread_id"] = thread_id
    return config


@pytest.fixture
def lessons_api(monkeypatch: pytest.MonkeyPatch) -> FakeLessonsApi:
    """Serve the lessons API from memory for every client the tools build."""

    fake = FakeLessonsApi()
    transport = httpx.MockTransport(fake.handle)
    monkeypatch.setattr(
        "tech_support_agent.lessons.client.default_transport", lambda: transport
    )
    return fake
