import asyncio
from pathlib import Path

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from pydantic import SecretStr, ValidationError

from tech_support_agent.bug_resolution.adapters.telegram import (
    TelegramClient,
    TelegramError,
    TelegramUpdate,
)
from tech_support_agent.bug_resolution.contracts import (
    EventReceipt,
    TeamButton,
    TeamMessage,
)
from tech_support_agent.config import InvestigationSettings
from tests.bug_resolution.test_investigation import ARTIFACT, FakeSandbox


async def test_photo_delivery_uses_actual_registered_png_then_buttons(
    tmp_path: Path,
) -> None:
    artifact = await FakeSandbox().screenshot(tmp_path)
    calls = []

    async def handler(request: web.Request) -> web.Response:
        method = request.match_info["method"]
        if method == "sendPhoto":
            body = await request.post()
            assert body["photo"].file.read() == artifact.path.read_bytes()
            assert body["chat_id"] == "42"
        else:
            body = await request.json()
            assert body["chat_id"] == 42
            assert (
                body["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
                == "opaque"
            )
        calls.append(method)
        return web.json_response(
            {
                "ok": True,
                "result": {
                    "message_id": len(calls),
                    "chat": {"id": 42, "type": "private"},
                },
            }
        )

    app = web.Application()
    app.router.add_post("/bottest/{method}", handler)
    async with TestServer(app) as server, ClientSession() as session:
        client = TelegramClient(
            session,
            SecretStr("test"),
            42,
            42,
            {ARTIFACT: artifact},
            tmp_path,
            api_root=str(server.make_url("")).rstrip("/"),
        )
        receipt = await client.send(
            TeamMessage(
                text="Evidence",
                artifact_ids=[ARTIFACT],
                buttons=[TeamButton(text="Approve", token="opaque")],
            )
        )
        assert receipt.message_id == 2 and calls == ["sendPhoto", "sendMessage"]
        with pytest.raises(TelegramError, match="Unknown"):
            await client.send(TeamMessage(text="bad", artifact_ids=["not-real"]))
        artifact.path.unlink()
        artifact.path.symlink_to(tmp_path / "elsewhere.png")
        with pytest.raises(TelegramError, match="path"):
            await client.send(TeamMessage(text="bad", artifact_ids=[ARTIFACT]))
        assert calls == ["sendPhoto", "sendMessage"]


@pytest.mark.parametrize(
    "result",
    [
        {"ok": False, "description": "SECRET-TOKEN"},
        {
            "ok": True,
            "result": {"message_id": 1, "chat": {"id": 99, "type": "private"}},
        },
    ],
)
async def test_transport_failure_or_wrong_target_never_acknowledges(
    tmp_path: Path, result: dict
) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.json_response(result)

    app = web.Application()
    app.router.add_post("/bottest/sendMessage", handler)
    async with TestServer(app) as server, ClientSession() as session:
        client = TelegramClient(
            session,
            SecretStr("test"),
            42,
            42,
            {},
            tmp_path,
            api_root=str(server.make_url("")).rstrip("/"),
        )
        with pytest.raises(TelegramError) as error:
            await client.send(TeamMessage(text="test"))
        assert "SECRET-TOKEN" not in str(error.value)


async def test_identity_private_target_and_webhook_are_verified(tmp_path: Path) -> None:
    webhook = ""

    async def handler(request: web.Request) -> web.Response:
        method = request.match_info["method"]
        result = {
            "getMe": {"id": 100, "is_bot": True},
            "getChat": {"id": 42, "type": "private"},
            "getWebhookInfo": {"url": webhook},
        }[method]
        return web.json_response({"ok": True, "result": result})

    app = web.Application()
    app.router.add_post("/bottest/{method}", handler)
    async with TestServer(app) as server, ClientSession() as session:
        client = TelegramClient(
            session,
            SecretStr("test"),
            42,
            42,
            {},
            tmp_path,
            api_root=str(server.make_url("")).rstrip("/"),
        )
        await client.identity()
        assert client.bot_id == 100
        webhook = "https://unrelated.example/webhook"
        with pytest.raises(TelegramError, match="configuration"):
            await client.identity()


async def test_polling_advances_offset_skips_duplicates_and_malformed_payload(
    tmp_path: Path,
) -> None:
    offsets = []
    received = []
    second_poll = asyncio.Event()
    update = {
        "update_id": 8,
        "message": {
            "message_id": 1,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False},
            "text": "desired behavior",
        },
    }

    async def handler(request: web.Request) -> web.Response:
        payload = await request.json()
        offsets.append(payload["offset"])
        if len(offsets) == 1:
            result = [{"update_id": 7, "message": "malformed"}, update, update]
        else:
            result = []
            second_poll.set()
        return web.json_response({"ok": True, "result": result})

    async def consume(event: object) -> EventReceipt:
        received.append(event)
        return EventReceipt(status="accepted")

    app = web.Application()
    app.router.add_post("/bottest/getUpdates", handler)
    async with TestServer(app) as server, ClientSession() as session:
        client = TelegramClient(
            session,
            SecretStr("test"),
            42,
            42,
            {},
            tmp_path,
            api_root=str(server.make_url("")).rstrip("/"),
        )
        task = asyncio.create_task(client.poll(consume, consume))
        try:
            await asyncio.wait_for(second_poll.wait(), 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert len(received) == 1 and offsets[:2] == [0, 9] and client.offset == 9


async def test_foreign_and_bot_text_has_no_graph_or_reply_side_effect(
    tmp_path: Path,
) -> None:
    async def consume(event: object) -> EventReceipt:
        raise AssertionError("Unauthorized update reached coordinator")

    async with ClientSession() as session:
        client = TelegramClient(session, SecretStr("test"), 42, 42, {}, tmp_path)
        for sender in ({"id": 99, "is_bot": False}, {"id": 42, "is_bot": True}):
            await client.handle_update(
                TelegramUpdate.model_validate(
                    {
                        "update_id": 1,
                        "message": {
                            "message_id": 2,
                            "chat": {"id": 42, "type": "private"},
                            "from": sender,
                            "text": "approve",
                        },
                    }
                ),
                consume,
                consume,
            )


def test_phase3_settings_have_no_model_fallback_and_require_explicit_targets(
    tmp_path: Path,
) -> None:
    values = {
        "model": "operator/verified-model",
        "model_api_key": "secret",
        "telegram_bot_token": "bot-secret",
        "telegram_user_id": 42,
        "telegram_chat_id": 42,
        "artifacts_dir": tmp_path,
    }
    settings = InvestigationSettings(_env_file=None, **values)
    assert settings.stage_tool_calls == 30 and settings.stage_seconds == 300
    assert settings.reviewer_tool_calls == 15 and settings.reviewer_seconds == 180
    assert settings.model == "operator/verified-model"
    for field in (
        "model",
        "model_api_key",
        "telegram_bot_token",
        "telegram_user_id",
        "telegram_chat_id",
        "artifacts_dir",
    ):
        with pytest.raises(ValidationError):
            InvestigationSettings(
                _env_file=None,
                **{key: value for key, value in values.items() if key != field},
            )
    with pytest.raises(ValidationError):
        InvestigationSettings(
            _env_file=None, **values, model_base_url="https://other.example"
        )
