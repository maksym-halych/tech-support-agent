import pytest

from tech_support_agent.bug_resolution.fix_contracts import FixPolicyError
from tests.bug_resolution.fix_fixtures import edit_and_freeze, plan, session


async def test_global_three_attempts_including_ci_and_reapproval() -> None:
    subject, _ = session()
    await subject.install_approval(plan())
    await edit_and_freeze(subject)
    decision = await subject.retry_or_handoff("CI failure", new_approach=True)
    assert decision.action == "reapprove" and decision.attempts_used == 1
    assert subject.approval is subject.candidate is subject.verification is None
    with pytest.raises(FixPolicyError):
        await subject.begin_attempt("second", 1)
    with pytest.raises(FixPolicyError):
        await subject.install_approval(plan())
    await subject.install_approval(plan(2))
    await edit_and_freeze(subject, "second")
    decision = await subject.retry_or_handoff("blocking review")
    assert decision.action == "retry" and decision.attempts_used == 2
    await edit_and_freeze(subject, "third")
    decision = await subject.retry_or_handoff("CI failure", new_approach=True)
    assert decision.action == "handoff" and decision.attempts_used == 3
    with pytest.raises(FixPolicyError):
        await subject.install_approval(plan(3))
    with pytest.raises(FixPolicyError):
        await subject.begin_attempt("fourth", 2)


async def test_repeated_failure_delivery_does_not_spend_attempt() -> None:
    subject, _ = session()
    await subject.install_approval(plan())
    await edit_and_freeze(subject)
    first = await subject.retry_or_handoff("failed")
    duplicate = await subject.retry_or_handoff("failed")
    assert first == duplicate and subject.attempts_used == 1


async def test_factory_failure_consumes_attempt_without_write_capability() -> None:
    from tech_support_agent.bug_resolution.fixing import FixSession
    from tests.bug_resolution.fix_fixtures import BASE_SHA

    async def unavailable():
        raise OSError("Docker unavailable")

    subject = FixSession("run", "case", BASE_SHA, unavailable)
    await subject.install_approval(plan())
    for attempt in range(1, 4):
        with pytest.raises(OSError):
            await subject.begin_attempt(str(attempt), 1)
        assert subject.attempts_used == attempt
        with pytest.raises(FixPolicyError):
            await subject.begin_attempt(str(attempt), 1)
    assert (await subject.retry_or_handoff("unavailable")).action == "handoff"
