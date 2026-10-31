"""Offline models and an isolated, deterministic lessons API."""

import json
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from lessons_api.app import create_app
from pydantic import SecretStr

from tech_support_agent.models import ApiConfig, ApiCredentials, SupportContext


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-placeholder")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


@pytest.fixture
def demo_app() -> Iterator[FastAPI]:
    app = create_app(clock=lambda: datetime(2026, 10, 31, 9, tzinfo=UTC))
    yield app
    app.state.store.close()


@pytest.fixture
def api_config(tmp_path: Path, demo_app: FastAPI) -> ApiConfig:
    path = tmp_path / "openapi.json"
    path.write_text(json.dumps(demo_app.openapi()), encoding="utf-8")
    return ApiConfig(
        openapi_path=path,
        base_url="http://demo.test",
        idempotency_header="Idempotency-Key",
    )


@pytest.fixture
def context() -> SupportContext:
    return SupportContext(
        user_id="alice",
        credentials=ApiCredentials(bearer_token=SecretStr("demo-alice")),
    )


@pytest.fixture
def transport(demo_app: FastAPI) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=demo_app)


class FakeToolCallingModel(GenericFakeChatModel):
    """A model replaying scripted responses; no provider requests are made."""

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> BaseChatModel:
        return self


def scripted_model(*messages: AIMessage) -> FakeToolCallingModel:
    return FakeToolCallingModel(messages=iter(messages))


def call(name: str, **args: Any) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call-{uuid4()}", "type": "tool_call"}
        ],
    )


def finish(message: str = "Готово", status: str = "completed") -> AIMessage:
    return call("finish", message=message, status=status)


def booking(slot: str = "iryna:2026-11-03:14") -> AIMessage:
    return call(
        "call_operation",
        operation_id="create_booking",
        body={"slot_id": slot},
        confirmation_summary="Записати до Ірини Коваль 3 листопада 2026 о 14:00 Europe/Kyiv",
    )
