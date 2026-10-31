"""Operator-only capability probes; never send chat messages, publish, or select a fallback."""

import argparse
import asyncio
import base64
import json
import logging
from pathlib import Path

from aiohttp import ClientSession
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool
from langsmith import tracing_context
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from tech_support_agent.bug_resolution.adapters.github import GitHubAdapter, HostCLI
from tech_support_agent.bug_resolution.adapters.sandbox import (
    DockerSandbox,
    capture_profile,
)
from tech_support_agent.bug_resolution.adapters.telegram import TelegramClient
from tech_support_agent.bug_resolution.agents import (
    StageLimits,
    create_bug_model,
    execute_stage,
    text_input,
)
from tech_support_agent.bug_resolution.contracts import Contract
from tech_support_agent.bug_resolution.publication_contracts import (
    GitHubCredentials,
    PublisherConfig,
)
from tech_support_agent.bug_resolution.sandbox.protocol import Empty, PageSnapshot
from tech_support_agent.config import DemoSettings, InvestigationSettings

logger = logging.getLogger(__name__)


class PublicationProbeSettings(BaseSettings):
    """Explicit host-only credentials for read-only publication preflight."""

    model_config = SettingsConfigDict(
        env_prefix="BUG_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    github_token: SecretStr
    github_workflow_id: int = Field(gt=0)

    @field_validator("github_token")
    @classmethod
    def required_token(cls, value: SecretStr) -> SecretStr:
        """Reject an empty token without printing it."""

        if not value.get_secret_value().strip():
            raise ValueError("Explicit GitHub token required")
        return value


class RepositoryAccess(BaseModel):
    """Read-only GitHub response projection; unknown metadata is not retained."""

    model_config = ConfigDict(extra="ignore", strict=True)
    full_name: str
    archived: bool
    permissions: dict[str, bool]


class WorkflowFile(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    type: str
    encoding: str
    content: str


async def publication_preflight(
    settings: PublicationProbeSettings, demo: DemoSettings
) -> None:
    """Check both remote/local bases, exact trusted workflow and readable CI without writes.

    Repository push permission is checked through read-only metadata, not a trial push.
    Fine-grained token scopes still require the operator's explicit confirmation.
    """

    profiles = [
        await capture_profile(demo, scenario) for scenario in ("continue", "both")
    ]
    if profiles[0].base_sha == profiles[1].base_sha:
        raise RuntimeError("Independent demo bases must have different SHAs")
    trusted = (
        Path(__file__).with_name("demo_template") / ".github/workflows/verify.yml"
    ).read_bytes()
    for profile in profiles:
        config = PublisherConfig(
            repo=demo.demo_repo,
            base_branch=f"demo/{profile.scenario}",
            workflow_id=settings.github_workflow_id,
            # HostCLI requires authorization even for reads. This probe exposes no writes.
            live_authorized=True,
        )
        cli = HostCLI(config, GitHubCredentials(token=settings.github_token))
        try:
            adapter = GitHubAdapter(cli)
            raw = await cli.command(
                "gh",
                (
                    "api",
                    "--hostname",
                    "github.com",
                    "--method",
                    "GET",
                    f"repos/{config.repo}",
                ),
            )
            access = RepositoryAccess.model_validate(json.loads(raw))
            if (
                access.full_name != config.repo
                or access.archived
                or not access.permissions.get("push")
            ):
                raise RuntimeError(
                    "Configured repository is not writable by this identity"
                )
            heads = await adapter.heads("fix/preflight-read-only")
            if heads.base_sha != profile.base_sha:
                raise RuntimeError("Local and remote demo base differ")
            identity = await adapter.workflow()
            if (
                identity.id != config.workflow_id
                or identity.path != config.workflow_path
                or identity.state != "active"
            ):
                raise RuntimeError(
                    "Expected workflow is absent, disabled or mismatched"
                )
            raw = await cli.command(
                "gh",
                (
                    "api",
                    "--hostname",
                    "github.com",
                    "--method",
                    "GET",
                    f"repos/{config.repo}/contents/{config.workflow_path}?ref={config.base_branch}",
                ),
            )
            file = WorkflowFile.model_validate(json.loads(raw))
            if (
                file.type != "file"
                or file.encoding != "base64"
                or base64.b64decode("".join(file.content.split()), validate=True)
                != trusted
            ):
                raise RuntimeError("Remote workflow differs from trusted harness")
            await adapter.runs()
        finally:
            cli.close()


class ProbeResult(Contract):
    """Structured response proving the configured model used a tool result."""

    value: int = Field(strict=True)


async def check_model(model: BaseChatModel) -> None:
    """Require both actual tool calling and a schema-validated final response."""

    @tool
    async def probe_value() -> str:
        """Return a harmless test value for the integration probe."""

        return "731"

    with tracing_context(enabled=False):
        run = await execute_stage(
            model,
            "Call probe_value, then submit_result with its integer value.",
            text_input("Verify tool calling and structured output; do not guess."),
            [probe_value],
            ProbeResult,
            StageLimits(model_turns=4, tool_calls=2, seconds=60),
        )
    if run.output.value != 731 or "probe_value" not in run.tool_names:
        raise RuntimeError(
            "Configured model did not demonstrate tool calling and structured output"
        )


async def investigation_preflight(
    settings: InvestigationSettings, demo: DemoSettings
) -> None:
    """Check both captured bases, actual Chromium, exact model and private Telegram identity.

    This command contacts the configured model and Telegram only when explicitly run.
    It sends no chat message, creates no PR, and never clicks approval on a human's behalf.
    """

    profiles = [
        await capture_profile(demo, scenario) for scenario in ("continue", "both")
    ]
    if profiles[0].base_sha == profiles[1].base_sha:
        raise RuntimeError("Independent demo bases must have different SHAs")
    for profile in profiles:
        sandbox = await DockerSandbox.prepare(profile)
        try:
            result = await sandbox.request(Empty(op="inspect"))
            if not isinstance(result, PageSnapshot):
                raise TypeError("Chromium did not produce a page snapshot")
            await sandbox.screenshot(settings.artifacts_dir)
        finally:
            await sandbox.close()
    await check_model(create_bug_model(settings))
    async with ClientSession() as session:
        telegram = TelegramClient(
            session,
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            settings.telegram_user_id,
            {},
            settings.artifacts_dir,
        )
        await telegram.identity()


def main() -> None:
    """Run only the explicitly requested preflight stage; report names, never secret values."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage", required=True, choices=["investigation", "publication", "all"]
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        demo = DemoSettings()
        settings = (
            InvestigationSettings() if args.stage in ("investigation", "all") else None
        )
        publication = (
            PublicationProbeSettings() if args.stage in ("publication", "all") else None
        )
    except ValidationError as error:
        fields = ", ".join(
            "BUG_" + str(item["loc"][0]).upper()
            for item in error.errors(include_input=False)
        )
        raise SystemExit(
            f"Preflight blocked: missing/invalid configuration: {fields}"
        ) from None
    try:

        async def run() -> None:
            if publication:
                await publication_preflight(publication, demo)
            if settings:
                await investigation_preflight(settings, demo)

        asyncio.run(run())
    except Exception:  # noqa: BLE001 - provider exceptions can contain credentials
        raise SystemExit(
            "Preflight blocked: verify configured model, Docker bases/image, Telegram target and repository/workflow access; no fallback used"
        ) from None
    logger.info(
        "Requested preflight stage passed; no chat message, branch, PR or approval was created"
    )


if __name__ == "__main__":
    main()
