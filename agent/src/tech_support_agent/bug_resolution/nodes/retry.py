"""One retry decision boundary for local failures, changed approach, and future CI."""

from tech_support_agent.bug_resolution.fix_contracts import RetryDecision
from tech_support_agent.bug_resolution.fixing import FixSession
from tech_support_agent.bug_resolution.sandbox.protocol import WireModel


class RetryRequest(WireModel):
    reason: str
    new_approach: bool = False


class RetryNode:
    """Never reset the global attempt ledger after reapproval or CI failure."""

    def __init__(self, session: FixSession) -> None:
        self.session = session

    async def __call__(self, request: RetryRequest) -> RetryDecision:
        """Revoke old evidence and return routing/report data, without claiming delivery."""

        return await self.session.retry_or_handoff(
            request.reason, new_approach=request.new_approach
        )
