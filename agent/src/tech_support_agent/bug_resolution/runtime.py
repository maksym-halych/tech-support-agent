"""Private investigation, editing, verification and publication resource ownership."""

import json
from collections.abc import Awaitable, Callable

from langchain_core.language_models import BaseChatModel

from tech_support_agent.bug_resolution.adapters.sandbox import (
    DemoProfile,
    DockerSandbox,
    ImageArtifact,
)
from tech_support_agent.bug_resolution.adapters.telegram import TelegramClient
from tech_support_agent.bug_resolution.agents import (
    StageLimits,
    execute_stage,
    text_input,
)
from tech_support_agent.bug_resolution.contracts import (
    CaseSnapshot,
    ExecutionOutcome,
    InvestigationResult,
    PlanProposal,
)
from tech_support_agent.bug_resolution.evidence import EvidenceStore
from tech_support_agent.bug_resolution.fixing import docker_editor_factory
from tech_support_agent.bug_resolution.graph import Phase3Services
from tech_support_agent.bug_resolution.model_stages import ModelStages
from tech_support_agent.bug_resolution.nodes.investigate import investigate_case
from tech_support_agent.bug_resolution.orchestration import ResolutionWorkflow
from tech_support_agent.bug_resolution.prompts import PLANNER_PROMPT
from tech_support_agent.bug_resolution.publication import Publisher
from tech_support_agent.bug_resolution.verification import (
    CandidateVerifier,
    DockerVerificationBackend,
)
from tech_support_agent.config import InvestigationSettings


class Phase3Runtime:
    """Keep clients, sandbox handles and filesystem paths out of checkpoints."""

    def __init__(
        self,
        settings: InvestigationSettings,
        profile: DemoProfile,
        model: BaseChatModel,
        telegram: TelegramClient,
        artifacts: dict[str, ImageArtifact],
        *,
        publisher: Publisher | None = None,
        workflow: ResolutionWorkflow | None = None,
        poll_interval: float = 10,
    ) -> None:
        self.settings = settings
        self.profile = profile
        self.model = model
        self.telegram = telegram
        self.artifacts = artifacts
        self.sandboxes: dict[str, DockerSandbox] = {}
        self.limits = StageLimits(
            model_turns=settings.model_turns,
            tool_calls=settings.stage_tool_calls,
            seconds=settings.stage_seconds,
        )
        stages = ModelStages(
            model,
            self.limits,
            StageLimits(
                model_turns=settings.model_turns,
                tool_calls=settings.reviewer_tool_calls,
                seconds=min(settings.reviewer_seconds, 180),
            ),
        )
        evidence = EvidenceStore(settings.artifacts_dir)
        verifier = CandidateVerifier(
            DockerVerificationBackend(profile, evidence, stages.browser),
            evidence,
            stages.review,
        )
        self.workflow = workflow or ResolutionWorkflow(
            docker_editor_factory(profile),
            stages.fix,
            verifier,
            publisher,
            artifacts,
            max_tools=settings.stage_tool_calls,
            max_seconds=settings.stage_seconds,
            poll_interval=poll_interval,
        )

    async def investigate(self, case: CaseSnapshot) -> InvestigationResult:
        """Prepare the captured base lazily and reuse it only within this case."""

        sandbox = self.sandboxes.get(case.case_id)
        if sandbox is None:
            sandbox = await DockerSandbox.prepare(self.profile)
            self.sandboxes[case.case_id] = sandbox
        result = await investigate_case(
            case,
            sandbox,
            self.model,
            self.settings.artifacts_dir,
            self.limits,
            self.artifacts,
        )
        self.workflow.register_reproduction(case, result)
        return result

    async def propose(self, case: CaseSnapshot) -> PlanProposal:
        """Use a fresh planner context with facts and authorized replies, not tool reasoning."""

        # Approval, attempts and run tokens are intentionally absent from model input.
        context = {
            "scenario": case.scenario,
            "investigation": case.investigation.model_dump(),
            "previous_plan": case.plan.model_dump() if case.plan else None,
            "discussion": case.discussion,
            "latest_reply": case.team_event.text if case.team_event else None,
            "previous_failure": case.retry_reason or None,
        }
        run = await execute_stage(
            self.model,
            PLANNER_PROMPT,
            text_input(json.dumps(context)),
            [],
            PlanProposal,
            self.limits,
        )
        return run.output

    async def cleanup(self, case_id: str) -> None:
        """Destroy only this case's container; keep actual evidence for inspection."""

        try:
            await self.workflow.cleanup(case_id)
        finally:
            sandbox = self.sandboxes.get(case_id)
            if sandbox:
                await sandbox.close()
                del self.sandboxes[case_id]

    async def close(self) -> None:
        """Retry cleanup of any remaining owned containers on worker shutdown."""

        await self.workflow.close()
        for case_id in list(self.sandboxes):
            await self.cleanup(case_id)

    def services(
        self,
        execute_approved: Callable[[CaseSnapshot], Awaitable[ExecutionOutcome]]
        | None = None,
    ) -> Phase3Services:
        """Use the integrated workflow unless an explicit legacy executor is injected."""

        return Phase3Services(
            investigate=self.investigate,
            propose=self.propose,
            send=self.telegram.send,
            telegram_user_id=self.settings.telegram_user_id,
            telegram_chat_id=self.settings.telegram_chat_id,
            scenario=self.profile.scenario,
            base_sha=self.profile.base_sha,
            execute_approved=execute_approved,
            cleanup=self.cleanup,
            workflow=None if execute_approved else self.workflow,
        )
