"""Opt-in real delivery only; never synthesize a human approval through Bot API."""

import secrets

import pytest
from aiohttp import ClientSession
from pydantic import ValidationError

from tech_support_agent.bug_resolution.adapters.sandbox import (
    DockerSandbox,
    capture_profile,
)
from tech_support_agent.bug_resolution.adapters.telegram import TelegramClient
from tech_support_agent.bug_resolution.contracts import TeamButton, TeamMessage
from tech_support_agent.config import BugSettings, DemoSettings, InvestigationSettings

pytestmark = pytest.mark.live


async def test_real_private_text_photo_and_buttons() -> None:
    try:
        settings = InvestigationSettings()
        demo = DemoSettings()
        worker = BugSettings()
    except ValidationError:
        pytest.skip(
            "Live delivery blocked: configured model/demo/private Telegram/artifact settings required"
        )
    profile = await capture_profile(demo, worker.scenario)
    sandbox = await DockerSandbox.prepare(profile)
    try:
        artifact = await sandbox.screenshot(settings.artifacts_dir)
    finally:
        await sandbox.close()
    async with ClientSession() as session:
        telegram = TelegramClient(
            session,
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            settings.telegram_user_id,
            {artifact.artifact_id: artifact},
            settings.artifacts_dir,
        )
        await telegram.identity()
        receipt = await telegram.send(
            TeamMessage(
                text="Phase 3 live delivery probe. This is an actual Docker screenshot, not a bug diagnosis. These probe buttons do not authorize code changes. Human approve/discuss/reject verification must use a running worker case separately.",
                artifact_ids=[artifact.artifact_id],
                buttons=[
                    TeamButton(text=f"Probe: {action}", token=secrets.token_urlsafe(24))
                    for action in ("approve", "discuss", "reject")
                ],
            )
        )
        assert receipt.message_id > 0
