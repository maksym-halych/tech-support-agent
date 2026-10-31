"""Bounded Bot API delivery and long polling, with no public resume endpoint."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, FormData
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from tech_support_agent.bug_resolution.adapters.sandbox import ImageArtifact
from tech_support_agent.bug_resolution.contracts import (
    EventReceipt,
    TeamCallback,
    TeamDelivery,
    TeamMessage,
    TeamReply,
)

logger = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    """Transport/configuration error with token-free messages."""


class TelegramObject(BaseModel):
    """Parse only needed Bot API fields; Telegram can add response fields."""

    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)


class TelegramUser(TelegramObject):
    id: int = Field(strict=True)
    is_bot: bool = Field(strict=True)


class TelegramChat(TelegramObject):
    id: int = Field(strict=True)
    type: str


class TelegramMessage(TelegramObject):
    message_id: int = Field(gt=0, strict=True)
    chat: TelegramChat
    sender: TelegramUser | None = Field(default=None, alias="from")
    text: str | None = None
    reply_to_message: TelegramMessage | None = None


class TelegramQuery(TelegramObject):
    id: str
    sender: TelegramUser = Field(alias="from")
    data: str | None = None
    message: TelegramMessage | None = None


class TelegramUpdate(TelegramObject):
    update_id: int = Field(ge=0, strict=True)
    message: TelegramMessage | None = None
    callback_query: TelegramQuery | None = None


class TelegramClient:
    """One configured private target; artifact paths come only from trusted receipts."""

    def __init__(
        self,
        session: ClientSession,
        token: SecretStr,
        chat_id: int,
        user_id: int,
        artifacts: dict[str, ImageArtifact],
        artifacts_dir: Path,
        *,
        api_root: str = "https://api.telegram.org",
    ) -> None:
        parsed = urlsplit(api_root)
        if api_root != "https://api.telegram.org" and not (
            parsed.scheme == "http"
            and parsed.hostname == "127.0.0.1"
            and parsed.port
            and not parsed.username
            and not parsed.password
            and parsed.path in ("", "/")
            and not parsed.query
            and not parsed.fragment
        ):
            raise ValueError(
                "Only Telegram or an injected loopback test server is allowed"
            )
        if chat_id <= 0 or user_id <= 0:
            raise ValueError("A configured private chat and user are required")
        self.session = session
        self._url = f"{api_root.rstrip('/')}/bot{token.get_secret_value()}"
        self.chat_id = chat_id
        self.user_id = user_id
        self.artifacts = artifacts
        self.artifacts_dir = artifacts_dir
        self.bot_id: int | None = None
        self.offset = 0

    async def _call(
        self, method: str, payload: dict[str, Any] | FormData, *, poll: bool = False
    ) -> Any:
        try:
            kwargs = (
                {"data": payload}
                if isinstance(payload, FormData)
                else {"json": payload}
            )
            async with self.session.post(
                f"{self._url}/{method}",
                **kwargs,
                timeout=ClientTimeout(total=40 if poll else 15),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise TelegramError("Telegram request failed")
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > 1_000_000:
                        raise TelegramError("Telegram response exceeded limit")
                body = json.loads(data)
                if (
                    not isinstance(body, dict)
                    or body.get("ok") is not True
                    or "result" not in body
                ):
                    raise TelegramError("Telegram did not acknowledge request")
                return body["result"]
        except ClientError, TimeoutError, ValueError:
            raise TelegramError("Telegram transport or response failure") from None

    async def identity(self) -> None:
        """Check bot identity, private target, and absence of a conflicting webhook."""

        user = TelegramUser.model_validate(await self._call("getMe", {}))
        chat = TelegramChat.model_validate(
            await self._call("getChat", {"chat_id": self.chat_id})
        )
        webhook = await self._call("getWebhookInfo", {})
        if (
            not user.is_bot
            or chat.id != self.chat_id
            or chat.type != "private"
            or chat.id != self.user_id
            or not isinstance(webhook, dict)
            or webhook.get("url") != ""
        ):
            raise TelegramError(
                "Telegram identity, private target, or polling configuration mismatch"
            )
        self.bot_id = user.id

    def _delivered(self, raw: Any) -> TeamDelivery:
        message = TelegramMessage.model_validate(raw)
        if message.chat.id != self.chat_id or message.chat.type != "private":
            raise TelegramError("Telegram delivered to an unexpected target")
        if self.bot_id is not None and (
            message.sender is None or message.sender.id != self.bot_id
        ):
            raise TelegramError("Telegram sender mismatch")
        return TeamDelivery(message_id=message.message_id)

    async def send(self, message: TeamMessage) -> TeamDelivery:
        """Send validated photos first, then the message whose ID owns the decisions."""

        for artifact_id in message.artifact_ids:
            artifact = self.artifacts.get(artifact_id)
            if artifact is None:
                raise TelegramError("Unknown evidence artifact")
            path = artifact.path
            if (
                path.parent != self.artifacts_dir
                or path.name != f"{artifact_id}.png"
                or any(part.is_symlink() for part in (path, *path.parents))
                or not path.is_file()
                or path.stat().st_size != artifact.size
                or artifact.size > 2_000_000
            ):
                raise TelegramError("Evidence path or size rejected")
            data = path.read_bytes()
            try:
                with Image.open(BytesIO(data)) as image:
                    if image.format != "PNG" or image.size != (
                        artifact.width,
                        artifact.height,
                    ):
                        raise ValueError("Evidence format mismatch")
                    image.verify()
            except ValueError, OSError:
                raise TelegramError("Invalid evidence image") from None
            form = FormData()
            form.add_field("chat_id", str(self.chat_id))
            form.add_field("caption", f"Actual reproduction evidence: {artifact_id}")
            form.add_field(
                "photo", data, filename=f"{artifact_id}.png", content_type="image/png"
            )
            self._delivered(await self._call("sendPhoto", form))
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": message.text,
            "link_preview_options": {"is_disabled": True},
        }
        if message.buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": button.text, "callback_data": button.token}]
                    for button in message.buttons
                ]
            }
        return self._delivered(await self._call("sendMessage", payload))

    async def acknowledge(self, callback_id: str) -> None:
        """Clear Telegram's spinner promptly, without claiming the click was approved."""

        result = await self._call(
            "answerCallbackQuery", {"callback_query_id": callback_id}
        )
        if result is not True:
            raise TelegramError("Callback acknowledgement failed")

    async def handle_update(
        self,
        update: TelegramUpdate,
        callback: Callable[[TeamCallback], Awaitable[EventReceipt]],
        reply: Callable[[TeamReply], Awaitable[EventReceipt]],
    ) -> None:
        """Normalize trusted API envelopes; authorize again in the coordinator."""

        query = update.callback_query
        if query is not None:
            try:
                await self.acknowledge(query.id)
            except TelegramError:
                logger.warning("Telegram callback acknowledgement unavailable")
            message = query.message
            if query.sender.is_bot or message is None or query.data is None:
                return
            if self.bot_id is not None and (
                message.sender is None or message.sender.id != self.bot_id
            ):
                return
            await callback(
                TeamCallback(
                    update_id=update.update_id,
                    callback_id=query.id,
                    token=query.data,
                    user_id=query.sender.id,
                    chat_id=message.chat.id,
                    chat_type=message.chat.type,
                    message_id=message.message_id,
                )
            )
        elif (
            (message := update.message) is not None
            and message.sender
            and not message.sender.is_bot
            and message.text
        ):
            if (
                message.sender.id != self.user_id
                or message.chat.id != self.chat_id
                or message.chat.type != "private"
            ):
                return
            target = message.reply_to_message
            receipt = await reply(
                TeamReply(
                    update_id=update.update_id,
                    user_id=message.sender.id,
                    chat_id=message.chat.id,
                    chat_type=message.chat.type,
                    reply_to_message_id=target.message_id if target else None,
                    text=message.text,
                )
            )
            if receipt.status == "stale":
                await self.send(
                    TeamMessage(
                        text="Use Discuss, then reply to the current bot discussion message. Text alone never approves a fix."
                    )
                )

    async def poll(
        self,
        callback: Callable[[TeamCallback], Awaitable[EventReceipt]],
        reply: Callable[[TeamReply], Awaitable[EventReceipt]],
    ) -> None:
        """Long-poll with process-local offset; restart never restores old capabilities."""

        while True:
            try:
                raw = await self._call(
                    "getUpdates",
                    {
                        "offset": self.offset,
                        "timeout": 30,
                        "limit": 50,
                        "allowed_updates": ["message", "callback_query"],
                    },
                    poll=True,
                )
                if not isinstance(raw, list) or len(raw) > 50:
                    raise TelegramError("Malformed update batch")
                for item in raw:
                    try:
                        update = TelegramUpdate.model_validate(item)
                    except ValidationError:
                        # A malformed payload with a valid envelope must not poison polling.
                        update_id = (
                            item.get("update_id") if isinstance(item, dict) else None
                        )
                        if type(update_id) is int and update_id >= self.offset:
                            self.offset = update_id + 1
                        logger.warning("Malformed Telegram update ignored")
                        continue
                    if update.update_id < self.offset:
                        continue
                    try:
                        await self.handle_update(update, callback, reply)
                    except ValidationError, TelegramError:
                        logger.warning(
                            "Telegram update rejected or delivery unavailable"
                        )
                    self.offset = update.update_id + 1
                if not raw:
                    await asyncio.sleep(0.1)
            except TelegramError, ValidationError:
                logger.warning("Telegram polling unavailable; no decision inferred")
                await asyncio.sleep(2)
