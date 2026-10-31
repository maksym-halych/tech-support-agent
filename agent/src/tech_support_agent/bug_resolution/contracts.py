"""Validated boundaries for Phase 1; no write or publication capabilities."""

import base64
import binascii
from io import BytesIO
from typing import Annotated, Literal, Self

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 1024 * 1024
HANDOFF_TEXT = "Your report has been passed to the team for review."
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[\w:-]+$")]
Text = Annotated[str, Field(min_length=1, max_length=16000, pattern=r"\S")]


class Contract(BaseModel):
    """Reject unknown command fields and keep validation input out of errors."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class TextAttachment(Contract):
    """An inline text attachment, never a URL to fetch."""

    type: Literal["text"] = "text"
    text: Text


class ImageAttachment(Contract):
    """A bounded, decoded image with a matching MIME type."""

    type: Literal["image"] = "image"
    mime_type: Literal["image/png", "image/jpeg", "image/gif", "image/webp"]
    data: str = Field(min_length=1, max_length=4 * ((MAX_IMAGE_BYTES + 2) // 3))

    @model_validator(mode="after")
    def validate_image(self) -> Self:
        """Validate base64, format, dimensions and actual image decoding."""

        try:
            raw = base64.b64decode(self.data, validate=True)
            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError("Image too large")
            with Image.open(BytesIO(raw)) as image:
                expected = {
                    "PNG": "image/png",
                    "JPEG": "image/jpeg",
                    "GIF": "image/gif",
                    "WEBP": "image/webp",
                }
                if (
                    expected.get(image.format) != self.mime_type
                    or image.width * image.height > 4_000_000
                ):
                    raise ValueError("Image format or dimensions invalid")
                image.verify()
            with Image.open(BytesIO(raw)) as image:
                image.load()
        except (
            binascii.Error,
            OSError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
        ) as exc:
            raise ValueError("Invalid inline image") from exc
        return self


Attachment = Annotated[TextAttachment | ImageAttachment, Field(discriminator="type")]
Attachments = Annotated[list[Attachment], Field(max_length=3)]


class CaseRequest(Contract):
    """An already-classified bug from a server-owned source thread."""

    source_thread_id: Identifier
    source_message_id: Identifier
    report: Text
    attachments: Attachments = Field(default_factory=list)


class CaseReceipt(Contract):
    """Admission outcome; busy never allocates a case."""

    status: Literal["accepted", "duplicate", "busy"]
    case_id: Identifier | None = None

    @model_validator(mode="after")
    def case_matches_status(self) -> Self:
        """Require a case exactly when admission succeeds or is duplicated."""

        if (self.status == "busy") != (self.case_id is None):
            raise ValueError("Invalid admission receipt")
        return self


class ClarificationRequest(Contract):
    """An answer correlated to one currently pending question."""

    source_thread_id: Identifier
    source_message_id: Identifier
    clarification_cursor: int = Field(gt=0, strict=True)
    answer: Text
    attachments: Attachments = Field(default_factory=list)


class EventReceipt(Contract):
    """Acknowledgement, not permission to resume an arbitrary graph."""

    status: Literal["accepted", "duplicate", "stale"]


class PublicEventQuery(Contract):
    """Thread binding is mandatory even for reads."""

    source_thread_id: Identifier
    after: int = Field(default=0, ge=0)


class Pending(Contract):
    """No undelivered event; no cursor advancement."""

    status: Literal["pending"] = "pending"


class Clarification(Contract):
    """The only private diagnostic output allowed in the public transcript."""

    status: Literal["clarification"] = "clarification"
    question: Text
    cursor: int = Field(gt=0, strict=True)


class HandedOff(Contract):
    """Emitted only after the team delivery dependency acknowledges success."""

    status: Literal["handed_off"] = "handed_off"
    text: Literal["Your report has been passed to the team for review."] = HANDOFF_TEXT
    cursor: int = Field(gt=0, strict=True)


PublicEvent = Annotated[
    Pending | Clarification | HandedOff, Field(discriminator="status")
]


class IntakeResult(Contract):
    """Minimal injected investigator result; details never cross the public API."""

    question: Text | None = None
    private_summary: Text


class DeliveryReceipt(Contract):
    """Explicit acknowledgement from the team delivery dependency."""

    delivered: bool = Field(strict=True)


class CaseSnapshot(Contract):
    """Private checkpoint payload, without clients or credentials."""

    run_id: Identifier
    case_id: Identifier
    request: CaseRequest
    clarification: ClarificationRequest | None = None
    result: IntakeResult | None = None
    clarification_cursor: int | None = None


class ClientWait(Contract):
    """Typed interrupt identity used only by the private coordinator."""

    case_id: Identifier
    clarification_cursor: int = Field(gt=0)
