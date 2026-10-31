"""Typed verification node for later shared graph wiring."""

from tech_support_agent.bug_resolution.fix_contracts import (
    FrozenCandidate,
    VerificationResult,
)
from tech_support_agent.bug_resolution.fixing import FixSession
from tech_support_agent.bug_resolution.verification import CandidateVerifier


class VerifyNode:
    """Keep services outside checkpoints and expose only typed results."""

    def __init__(self, session: FixSession, verifier: CandidateVerifier) -> None:
        self.session, self.verifier = session, verifier

    async def __call__(self, candidate: FrozenCandidate) -> VerificationResult:
        """Verify the current frozen candidate, never the editable workspace."""

        return await self.verifier.verify(self.session, candidate)
