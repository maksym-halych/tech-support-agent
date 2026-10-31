"""Injectable fix-stage node, independent of the shared Phase 3 state schema."""

import asyncio
import logging
from typing import Literal, Protocol

from tech_support_agent.bug_resolution.fix_contracts import (
    ApprovedFixPlan,
    FixPolicyError,
    FixResult,
    FrozenCandidate,
)
from tech_support_agent.bug_resolution.fixing import FixSession, ScopedFixTools
from tech_support_agent.bug_resolution.sandbox.protocol import WireModel

logger = logging.getLogger(__name__)


class Fixer(Protocol):
    async def __call__(
        self, plan: ApprovedFixPlan, tools: ScopedFixTools
    ) -> FixResult: ...


class FixRequest(WireModel):
    operation_id: str
    plan_revision: int


class FixNodeResult(WireModel):
    status: Literal["candidate", "needs_reapproval", "blocked"]
    summary: str
    candidate: FrozenCandidate | None = None


class FixNode:
    """Serialize and deduplicate invocation, with a hard overall fixer deadline."""

    def __init__(self, session: FixSession, fixer: Fixer) -> None:
        self.session, self.fixer = session, fixer
        self._lock = asyncio.Lock()
        self._results: dict[str, tuple[FixRequest, FixNodeResult]] = {}

    async def __call__(self, request: FixRequest) -> FixNodeResult:
        """Begin once, execute with scoped tools, revoke writers, and freeze a candidate."""

        request = FixRequest.model_validate(request)
        async with self._lock:
            previous = self._results.get(request.operation_id)
            if previous:
                if previous[0] != request:
                    return FixNodeResult(
                        status="blocked", summary="Attempt replay changed revision"
                    )
                if previous[1].candidate is not None:
                    try:
                        self.session.require_current(previous[1].candidate)
                    except FixPolicyError:
                        return FixNodeResult(
                            status="blocked",
                            summary="Replayed candidate is no longer current",
                        )
                return previous[1]
            started = False
            try:
                tools = await self.session.begin_attempt(
                    request.operation_id, request.plan_revision
                )
                started = True
                plan = self.session.approval
                assert plan is not None
                async with asyncio.timeout(self.session.max_seconds):
                    result = FixResult.model_validate(await self.fixer(plan, tools))
                    if result.status == "candidate":
                        candidate = await self.session.freeze(tools)
                        output = FixNodeResult(
                            status="candidate",
                            summary=result.summary,
                            candidate=candidate,
                        )
                    else:
                        await self.session.retry_or_handoff(
                            result.summary,
                            new_approach=result.status == "needs_reapproval",
                        )
                        output = FixNodeResult(
                            status=result.status, summary=result.summary
                        )
            except asyncio.CancelledError:
                await self.session.close()
                raise
            except Exception:
                logger.exception("Private fix stage failed")
                if started:
                    await self.session.retry_or_handoff(
                        "Fix stage failed or exhausted its budget"
                    )
                output = FixNodeResult(
                    status="blocked", summary="Fix stage failed or exhausted its budget"
                )
            self._results[request.operation_id] = (request, output)
            return output
