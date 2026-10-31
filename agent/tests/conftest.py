import os
import socket
from collections.abc import Iterator, Sequence
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from tech_support_agent.config import BugSettings, DemoSettings, Settings, get_settings


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

    The OpenAI client refuses to construct without one, which ``build_agent``
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
    """Pin the settings ``build_agent`` reads.

    The developer's .env is ignored, so a local model id or key cannot change
    what the tests see. Tests adjust the returned object.
    """

    if request.node.get_closest_marker("live"):
        return None
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
