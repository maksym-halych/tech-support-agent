"""Public graph nodes, isolated from private snapshots and service errors."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, TypedDict
from uuid import uuid4

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.message import add_messages
from pydantic import Field, TypeAdapter, ValidationError

from tech_support_agent.bug_resolution.adapters.private_api import (
    PrivateAPI,
    WorkerUnavailable,
)
from tech_support_agent.bug_resolution.contracts import (
    Attachments,
    CaseRequest,
    Clarification,
    ClarificationRequest,
    Contract,
    HandedOff,
    Identifier,
    ImageAttachment,
    Pending,
    PublicEventQuery,
)

UNAVAILABLE_TEXT = (
    "I can't pass your report to the team right now. Please try again later."
)
PENDING_TEXT = (
    "Your report is still being checked. It has not yet been passed to the team."
)
BUSY_TEXT = "Another report is being handled. Please try again later."
INVALID_TEXT = "Please send a text report with optional inline images; this attachment or message is not supported."


class BridgeBookkeeping(Contract):
    """Opaque delivery metadata only, safe to expose in public checkpoints."""

    case_id: Identifier | None = None
    cursor: int = Field(default=0, ge=0)
    clarification_cursor: int | None = None
    last_message_id: str | None = None
    handed_off: bool = False


class PublicInput(TypedDict):
    """Only messages can enter through normal graph input."""

    messages: Annotated[list[AnyMessage], add_messages]


class BridgeState(PublicInput, total=False):
    """Messages plus opaque bookkeeping, never a private subgraph."""

    bridge: dict


def normalize_messages(state: BridgeState) -> dict:
    """Persist human IDs in a separate node before any HTTP side effect."""

    return {
        "messages": [
            message.model_copy(update={"id": str(uuid4())})
            for message in state.get("messages", [])
            if isinstance(message, HumanMessage) and not message.id
        ]
    }


def _content(message: HumanMessage) -> tuple[str, Attachments]:
    if isinstance(message.content, str):
        return message.content, []
    texts: list[str] = []
    images: list[ImageAttachment] = []
    for block in message.content:
        if not isinstance(block, dict):
            raise TypeError("Unsupported content")
        if block.get("type") == "text" and set(block) <= {"type", "text"}:
            texts.append(block["text"])
        elif block.get("type") == "image" and set(block) <= {
            "type",
            "mimeType",
            "data",
            "metadata",
        }:
            metadata = block.get("metadata", {})
            if not isinstance(metadata, dict) or set(metadata) - {"name"}:
                raise ValueError("Unsupported image metadata")
            images.append(
                ImageAttachment(mime_type=block["mimeType"], data=block["data"])
            )
        else:
            raise ValueError("Only inline text/images are supported")
    return "\n".join(texts), TypeAdapter(Attachments).validate_python(images)


class PublicBridge:
    """Reusable orchestrator node; add normalize_messages immediately before it."""

    def __init__(
        self,
        api: PrivateAPI,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.api = api
        self.clock = clock
        self.sleep = sleep

    async def __call__(self, state: BridgeState, config: RunnableConfig) -> dict:
        """Forward a report or a new answer and return only safe AI messages.

        Raises:
            ValueError: When a server runtime thread ID is absent.
        """

        thread_id = config.get("configurable", {}).get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError("A server runtime thread ID is required")
        bookkeeping = BridgeBookkeeping.model_validate(state.get("bridge", {}))
        messages = state.get("messages", [])
        human = next(
            (
                message
                for message in reversed(messages)
                if isinstance(message, HumanMessage)
            ),
            None,
        )
        if human is None:
            return {}
        if not human.id:
            raise ValueError("Normalize message IDs before calling the bridge")

        def reply(text: str, message_id: str | None = None) -> dict:
            return {
                "messages": [
                    AIMessage(
                        content=text, id=message_id or f"bridge-notice-{human.id}"
                    )
                ],
                "bridge": bookkeeping.model_dump(),
            }

        try:
            text, attachments = _content(human)
            if not text.strip():
                raise ValueError("A text description is required")
            # Validate the entire input even on a duplicate; never partly admit it.
            intake = CaseRequest(
                source_thread_id=thread_id,
                source_message_id=human.id,
                report=text,
                attachments=attachments,
            )
            if bookkeeping.case_id is None:
                receipt = await self.api.accept_case(intake)
                if receipt.status == "busy":
                    return reply(BUSY_TEXT)
                bookkeeping = bookkeeping.model_copy(
                    update={"case_id": receipt.case_id, "last_message_id": human.id}
                )
            elif (
                bookkeeping.clarification_cursor
                and human.id != bookkeeping.last_message_id
            ):
                question_id = (
                    f"bridge-{bookkeeping.case_id}-{bookkeeping.clarification_cursor}"
                )
                human_index = max(
                    index
                    for index, message in enumerate(messages)
                    if message.id == human.id
                )
                question_seen = any(
                    message.id == question_id and isinstance(message, AIMessage)
                    for message in messages[:human_index]
                )
                if question_seen:
                    receipt = await self.api.clarify(
                        bookkeeping.case_id,
                        ClarificationRequest(
                            source_thread_id=thread_id,
                            source_message_id=human.id,
                            clarification_cursor=bookkeeping.clarification_cursor,
                            answer=text,
                            attachments=attachments,
                        ),
                    )
                    if receipt.status != "stale":
                        bookkeeping = bookkeeping.model_copy(
                            update={"last_message_id": human.id}
                        )
            if bookkeeping.handed_off:
                return {"bridge": bookkeeping.model_dump()}
            deadline = self.clock() + self.api.settings.poll_timeout
            async with asyncio.timeout(self.api.settings.poll_timeout):
                while True:
                    event = await self.api.public_event(
                        bookkeeping.case_id,
                        PublicEventQuery(
                            source_thread_id=thread_id, after=bookkeeping.cursor
                        ),
                    )
                    if not isinstance(event, Pending):
                        if event.cursor <= bookkeeping.cursor:
                            raise WorkerUnavailable()
                        bookkeeping = bookkeeping.model_copy(
                            update={
                                "cursor": event.cursor,
                                "clarification_cursor": event.cursor
                                if isinstance(event, Clarification)
                                else None,
                                "handed_off": isinstance(event, HandedOff),
                            }
                        )
                        return reply(
                            event.question
                            if isinstance(event, Clarification)
                            else event.text,
                            f"bridge-{bookkeeping.case_id}-{event.cursor}",
                        )
                    if self.clock() >= deadline:
                        return reply(PENDING_TEXT)
                    await self.sleep(self.api.settings.poll_interval)
        except ValidationError, ValueError, KeyError, TypeError:
            return reply(INVALID_TEXT)
        except WorkerUnavailable:
            return reply(UNAVAILABLE_TEXT)
        except TimeoutError:
            return reply(PENDING_TEXT)
