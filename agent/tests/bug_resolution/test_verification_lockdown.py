"""Emergency runtime lockdown is unconditional, not a reporter/hash mitigation."""

from pathlib import Path

import pytest

from tech_support_agent.bug_resolution.adapters.sandbox import (
    DemoProfile,
    DockerSandbox,
)
from tech_support_agent.bug_resolution.evidence import EvidenceStore
from tech_support_agent.bug_resolution.verification import (
    CandidateVerifier,
    DockerVerificationBackend,
    VerificationUnavailable,
)
from tests.bug_resolution.fix_fixtures import BASE_SHA, edit_and_freeze, plan, session


async def test_runtime_blocks_before_execution_or_passed_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even apparently valid candidates cannot execute or produce runtime evidence."""

    async def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Disabled runtime must not execute Docker, browser, or reviewer")

    monkeypatch.setattr(DockerSandbox, "prepare", forbidden)
    subject, _ = session()
    await subject.install_approval(plan())
    candidate = await edit_and_freeze(subject)
    store = EvidenceStore(tmp_path)
    backend = DockerVerificationBackend(
        DemoProfile(
            repo="fixture/quiz",
            directory=tmp_path,
            scenario="both",
            base_sha=BASE_SHA,
            image="unused",
        ),
        store,
        forbidden,
    )
    for kind in ("regression_on_base", "tests", "typecheck", "build"):
        with pytest.raises(
            VerificationUnavailable, match="Runtime verification blocked"
        ):
            await backend.check(candidate, kind)
    with pytest.raises(VerificationUnavailable):
        await backend.browser(candidate, plan())
    verifier = CandidateVerifier(backend, store, forbidden)
    result = await verifier.verify(subject, candidate)
    assert result.status == "blocked" and result.bundle is None and not result.checks
    assert "forge reporter stdout" in result.reason
    assert subject.verification is None and not list(tmp_path.iterdir())
    with pytest.raises(VerificationUnavailable):
        verifier.validate(None, candidate, plan())
