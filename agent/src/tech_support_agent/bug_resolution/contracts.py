"""Validated intake, investigation and human approval boundaries; no write tools."""

import base64
import binascii
from io import BytesIO
from typing import Annotated, Literal, Self

from PIL import Image, UnidentifiedImageError
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from tech_support_agent.bug_resolution.publication_contracts import (
    CIObservation,
    PublicationRef,
)
from tech_support_agent.bug_resolution.sandbox.protocol import safe_path

MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 1024 * 1024
MAX_TEXT_CHARACTERS = 16000
# web.json_response escapes astral characters as two six-byte surrogate escapes.
# Reserve another KiB for the public-event envelope and worker-generated cursor.
MAX_PUBLIC_RESPONSE_BYTES = 12 * MAX_TEXT_CHARACTERS + 1024
HANDOFF_TEXT = "Your report has been passed to the team for review."
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[\w:-]+$")]
Text = Annotated[
    str, Field(min_length=1, max_length=MAX_TEXT_CHARACTERS, pattern=r"\S")
]


def application_path(value: str) -> str:
    """Require an explicit application file compatible with the downstream write gate."""

    safe_path(value)
    if not value.startswith("src/"):
        raise ValueError("Approval scope must name application source files")
    return value


ApplicationPath = Annotated[str, AfterValidator(application_path)]


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


class InvestigationResult(Contract):
    """Model diagnosis; artifact IDs must also be checked against tool receipts."""

    status: Literal["confirmed", "needs_details", "unconfirmed", "blocked"]
    summary: Text
    steps: list[Text] = Field(default_factory=list, max_length=20)
    artifact_ids: list[Identifier] = Field(default_factory=list, max_length=10)
    source_paths: list[ApplicationPath] = Field(default_factory=list, max_length=20)
    question: Text | None = None

    @model_validator(mode="after")
    def evidence_required(self) -> Self:
        """Require evidence for confirmation and a question for clarification."""

        if self.status == "confirmed" and (not self.steps or not self.artifact_ids):
            raise ValueError("Confirmation requires steps and real artifacts")
        if self.status == "needs_details" and self.question is None:
            raise ValueError("Clarification requires a question")
        return self


class PlanProposal(Contract):
    """Model-owned proposal, deliberately without revision or approval fields."""

    expected_behavior: Text
    allowed_changes: list[ApplicationPath] = Field(min_length=1, max_length=20)
    acceptance_checks: list[Text] = Field(min_length=1, max_length=20)
    needs_discussion: bool = Field(default=False, strict=True)
    product_choice: Literal["combined", "separate"] | None = None


class ApprovedPlan(Contract):
    """Trusted handoff to a downstream executor, minted only from a button event."""

    revision: int = Field(gt=0, strict=True)
    expected_behavior: Text
    allowed_changes: list[ApplicationPath] = Field(min_length=1, max_length=20)
    acceptance_checks: list[Text] = Field(min_length=1, max_length=20)
    approved_by: int = Field(gt=0, strict=True)


class ExecutionOutcome(Contract):
    """Downstream terminal acknowledgement, not a claim of publication or CI."""

    status: Literal["completed", "blocked", "failed"]
    summary: Text


class TeamWait(Contract):
    """Correlation for exactly one current Telegram message and plan revision."""

    kind: Literal["team"] = "team"
    run_id: Identifier
    case_id: Identifier
    plan_revision: int = Field(gt=0, strict=True)
    message_id: int = Field(gt=0, strict=True)
    mode: Literal["decision", "discussion"]


class TeamEvent(Contract):
    """Coordinator-validated event; this is never accepted by the public API."""

    wait: TeamWait
    action: Literal["approve", "discuss", "reject", "reply"]
    user_id: int = Field(gt=0, strict=True)
    text: Text | None = None

    @model_validator(mode="after")
    def reply_has_text(self) -> Self:
        """Keep decision and reply payloads distinct."""

        if (self.action == "reply") != (self.text is not None):
            raise ValueError("Only replies require text")
        return self


class TeamCallback(Contract):
    """Normalized Telegram callback, before authorization."""

    update_id: int = Field(ge=0, strict=True)
    callback_id: str = Field(min_length=1, max_length=256)
    token: str = Field(min_length=1, max_length=64)
    user_id: int = Field(strict=True)
    chat_id: int = Field(strict=True)
    chat_type: str
    message_id: int = Field(gt=0, strict=True)


class TeamReply(Contract):
    """Normalized Telegram text, not permission to execute."""

    update_id: int = Field(ge=0, strict=True)
    user_id: int = Field(strict=True)
    chat_id: int = Field(strict=True)
    chat_type: str
    reply_to_message_id: int | None = None
    text: Text


class TeamButton(Contract):
    """Opaque capability token rendered by the transport."""

    text: str = Field(min_length=1, max_length=64)
    token: str = Field(min_length=1, max_length=64)


class TeamMessage(Contract):
    """A bounded plain-text message with trusted artifact IDs, never model paths."""

    text: str = Field(min_length=1, max_length=4000)
    buttons: list[TeamButton] = Field(default_factory=list, max_length=3)
    artifact_ids: list[Identifier] = Field(default_factory=list, max_length=10)


class TeamDelivery(Contract):
    """The actual bot message to which all current decisions must refer."""

    message_id: int = Field(gt=0, strict=True)


class CaseSnapshot(Contract):
    """Private checkpoint payload, without clients or credentials."""

    run_id: Identifier
    case_id: Identifier
    request: CaseRequest
    clarification: ClarificationRequest | None = None
    result: IntakeResult | None = None
    clarification_cursor: int | None = None
    scenario: Literal["continue", "both"] = "continue"
    phase: Literal[
        "investigating",
        "planning",
        "waiting_team",
        "approved",
        "rejected",
        "finished",
        "fixing",
        "verified",
        "waiting_ci",
    ] = "investigating"
    investigation: InvestigationResult | None = None
    base_sha: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")
    plan: PlanProposal | None = None
    plan_revision: int = Field(default=0, ge=0, strict=True)
    approval: ApprovedPlan | None = None
    attempt_count: int = Field(default=0, ge=0, le=3, strict=True)
    team_wait: TeamWait | None = None
    team_event: TeamEvent | None = None
    discussion: list[Text] = Field(default_factory=list, max_length=20)
    outcome: ExecutionOutcome | None = None
    handed_off: bool = False
    publication: PublicationRef | None = None
    ci_observation: CIObservation | None = None
    workflow_route: (
        Literal[
            "fix",
            "verify",
            "retry",
            "publish",
            "wait_ci",
            "wait_publication",
            "reapprove",
            "mark_ready",
            "finish",
        ]
        | None
    ) = None
    retry_reason: str = ""
    retry_new_approach: bool = False
    after_artifact_id: Identifier | None = None


class PublicationWait(Contract):
    """Reconcile one uncertain publication without spending another fix attempt."""

    kind: Literal["publication"] = "publication"
    run_id: Identifier
    case_id: Identifier
    attempt: int = Field(ge=1, le=3)
    revision: int = Field(gt=0)


class ClientWait(Contract):
    """Typed interrupt identity used only by the private coordinator."""

    case_id: Identifier
    clarification_cursor: int = Field(gt=0)
