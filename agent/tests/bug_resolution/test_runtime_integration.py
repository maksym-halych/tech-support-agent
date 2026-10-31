"""Offline private graph tests: real approval/ledger/verifier/export, scripted external ports."""

import asyncio
import base64
import io
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest
from PIL import Image

from tech_support_agent.bug_resolution.adapters.sandbox import export_png
from tech_support_agent.bug_resolution.contracts import (
    CaseRequest,
    InvestigationResult,
    PlanProposal,
    TeamCallback,
    TeamDelivery,
    TeamMessage,
)
from tech_support_agent.bug_resolution.coordinator import CaseCoordinator
from tech_support_agent.bug_resolution.evidence import EvidenceStore
from tech_support_agent.bug_resolution.fix_contracts import FileChange, FixResult
from tech_support_agent.bug_resolution.graph import Phase3Services
from tech_support_agent.bug_resolution.orchestration import ResolutionWorkflow
from tech_support_agent.bug_resolution.publication import can_publish
from tech_support_agent.bug_resolution.publication_contracts import (
    CIObservation,
    PublicationPending,
    PublicationRef,
)
from tech_support_agent.bug_resolution.sandbox.protocol import Screenshot, SourceFile
from tech_support_agent.bug_resolution.verification import CandidateVerifier
from tests.bug_resolution.fix_fixtures import BASE_SHA, MemoryEditor, plan
from tests.bug_resolution.test_ci import green
from tests.bug_resolution.test_publication import make_harness
from tests.bug_resolution.test_verification import bundle_for


async def eventually(predicate: Callable[[], object]) -> None:
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.001)


class ScriptedPublisher:
    """No Git, HTTP or production success simulation; only test port responses."""

    def __init__(self) -> None:
        self.publications = []
        self.exports = []
        self.ci_status = "passed"
        self.fail_attempts: set[int] = set()
        self.publish_pending = 0
        self.publish_calls = 0
        self.polls = 0
        self.ready_calls = 0
        self.ready_status = "passed"

    async def publish(self, authority, export, previous=None):
        can_publish(authority, export)
        self.publish_calls += 1
        if self.publish_pending:
            self.publish_pending -= 1
            raise PublicationPending("fixture transport uncertainty")
        publication = PublicationRef(
            run_id=authority.run_id,
            candidate=export.candidate,
            repo="fixture/demo",
            base_branch="demo/continue",
            branch=f"fix/{authority.case_id}",
            pr_number=7,
            head_sha=str(export.candidate.attempt) * 40,
            workflow_id=12,
            workflow_path=".github/workflows/verify.yml",
            required_jobs=("tests", "build"),
            pr_url="https://github.com/fixture/demo/pull/7",
        )
        if previous:
            assert previous.pr_number == publication.pr_number
            assert previous.candidate.attempt < publication.candidate.attempt
        self.publications.append(publication)
        self.exports.append(export)
        return publication

    async def inspect_ci(self, publication):
        self.polls += 1
        status = (
            "failed"
            if publication.candidate.attempt in self.fail_attempts
            else self.ci_status
        )
        return CIObservation(
            publication=publication,
            status=status,
            reason=f"fixture CI {status}",
            run_id=900 + publication.candidate.attempt,
            run_attempt=1,
        )

    async def mark_ready(self, authority, export, publication):
        can_publish(authority, export)
        assert export.candidate == publication.candidate
        self.ready_calls += 1
        return CIObservation(
            publication=publication,
            status=self.ready_status,
            reason="fixture readiness recheck",
            run_id=901,
            run_attempt=1,
        )


class Harness:
    def __init__(
        self,
        directory: Path,
        *,
        local_failures=(),
        reapprove=(),
        base_sha=BASE_SHA,
        base_files=None,
        source_path="src/grade.ts",
    ) -> None:
        self.messages: list[TeamMessage] = []
        self.editors: list[MemoryEditor] = []
        self.inputs: list[tuple[int, str]] = []
        self.bundles = {}
        self.publisher = ScriptedPublisher()
        self.store = EvidenceStore(directory)
        self.hold_draft = asyncio.Event()
        self.hold_draft.set()
        self.draft_sending = asyncio.Event()
        self.tamper_before = False
        self.artifacts = {}

        async def factory():
            editor = MemoryEditor()
            if base_files:
                editor.initial_files = base_files
                editor.files = {file.path: file for file in base_files}
            self.editors.append(editor)
            return editor

        async def fixer(approved, tools, feedback):
            self.inputs.append((approved.revision, feedback))
            if tools.receipt.attempt in reapprove:
                return FixResult(
                    status="needs_reapproval",
                    summary="A different approach requires a revised plan",
                )
            await tools.apply_patch(
                FileChange(
                    path=source_path,
                    content=f"fixed attempt {tools.receipt.attempt}",
                )
            )
            await tools.apply_patch(
                FileChange(
                    path="tests/regression.spec.ts", content="fixture UI behavior test"
                )
            )
            return FixResult(
                status="candidate",
                summary="Changed scoped source and added a regression",
            )

        owner = self

        class Backend:
            async def check(self, candidate, kind):
                if candidate.ref.digest not in owner.bundles:
                    owner.bundles[candidate.ref.digest] = bundle_for(
                        candidate, owner.store
                    )
                bundle = owner.bundles[candidate.ref.digest]
                check = getattr(bundle, {"tests": "tests_on_candidate"}.get(kind, kind))
                if candidate.ref.attempt in local_failures and kind == "tests":
                    return check.model_copy(update={"status": "failed", "exit_code": 1})
                return check

            async def browser(self, candidate, approved):
                return owner.bundles[candidate.ref.digest].browser

        async def reviewer(context):
            return self.bundles[context.candidate.ref.digest].review

        verifier = CandidateVerifier(Backend(), self.store, reviewer)
        self.workflow = ResolutionWorkflow(
            factory, fixer, verifier, self.publisher, self.artifacts, poll_interval=0.01
        )

        async def investigate(case):
            # A real decoded PNG is a fixture observation, never claimed as a live screenshot.
            output = io.BytesIO()
            Image.new("RGB", (2, 2), "blue").save(output, "PNG")
            png = output.getvalue()
            artifact = export_png(
                Screenshot(
                    artifact_id="a" * 32,
                    mime="image/png",
                    data=base64.b64encode(png).decode(),
                ),
                directory,
            )
            self.artifacts[artifact.artifact_id] = artifact
            result = InvestigationResult(
                status="confirmed",
                summary="Fixture reproduction",
                steps=["Select grade; observe disabled Continue"],
                artifact_ids=[artifact.artifact_id],
            )
            self.workflow.register_reproduction(case, result)
            return result

        async def propose(case):
            approved = plan()
            return PlanProposal(
                expected_behavior=approved.expected_behavior,
                allowed_changes=[source_path],
                acceptance_checks=list(approved.acceptance_checks),
            )

        async def send(message):
            self.messages.append(message)
            if "Draft PR:" in message.text:
                self.draft_sending.set()
                if self.tamper_before:
                    next(iter(self.artifacts.values())).path.write_bytes(b"tampered")
                await self.hold_draft.wait()
            return TeamDelivery(message_id=len(self.messages))

        self.coordinator = CaseCoordinator(
            Phase3Services(
                investigate=investigate,
                propose=propose,
                send=send,
                telegram_user_id=123,
                telegram_chat_id=123,
                base_sha=base_sha,
                workflow=self.workflow,
                cleanup=self.workflow.cleanup,
            ),
            id_source=iter(("run", "case")).__next__,
        )

    async def start(self):
        await self.coordinator.accept_case(
            CaseRequest(
                source_thread_id="thread",
                source_message_id="report",
                report="Continue does not work",
            )
        )
        await eventually(lambda: self.coordinator.records["case"].team_wait)
        assert not self.editors and not self.publisher.publications

    async def decide(self, action: Literal["approve", "reject"] = "approve", update=1):
        record = self.coordinator.records["case"]
        wait = record.team_wait
        token = next(
            token
            for token, binding in self.coordinator.tokens.items()
            if binding.action == action
        )
        event = TeamCallback(
            update_id=update,
            callback_id=f"callback-{update}",
            token=token,
            user_id=123,
            chat_id=123,
            chat_type="private",
            message_id=wait.message_id,
        )
        assert (await self.coordinator.consume_callback(event)).status == "accepted"
        return event

    async def finished(self):
        record = self.coordinator.records["case"]
        await asyncio.wait_for(asyncio.shield(record.task), 5)
        assert not record.failed
        assert self.coordinator.active is None
        return record.final


async def test_approved_graph_reaches_verified_export_ci_and_ready(tmp_path):
    harness = Harness(tmp_path)
    await harness.start()
    await harness.decide()
    final = await harness.finished()
    assert final.outcome.status == "completed"
    assert final.attempt_count == 1
    assert harness.publisher.publish_calls == harness.publisher.ready_calls == 1
    export = harness.publisher.exports[0]
    assert export.candidate.snapshot_digest != export.candidate.digest
    assert {file.path for file in export.files} == {
        "src/grade.ts",
        "tests/smoke.spec.ts",
        "tests/regression.spec.ts",
    }
    assert export.evidence.before_png != export.evidence.after_png
    assert len(harness.messages[-1].artifact_ids) == 2
    assert "No merge or production deployment" in harness.messages[-1].text
    assert all(editor.closed for editor in harness.editors)
    assert [event.status for event in harness.coordinator.records["case"].events] == [
        "handed_off"
    ]


async def test_local_and_ci_failures_share_three_attempts(tmp_path):
    harness = Harness(tmp_path, local_failures=(1,))
    harness.publisher.fail_attempts = {2, 3}
    await harness.start()
    await harness.decide()
    final = await harness.finished()
    assert final.attempt_count == len(harness.editors) == 3
    assert final.outcome.status == "blocked"
    assert "three-attempt budget is exhausted" in final.outcome.summary
    assert [pub.candidate.attempt for pub in harness.publisher.publications] == [2, 3]
    assert harness.publisher.ready_calls == 0
    assert "tests: failed" in harness.inputs[1][1]
    assert "CI failed" in harness.inputs[2][1]


async def test_changed_approach_needs_new_approval_without_resetting_attempts(tmp_path):
    harness = Harness(tmp_path, reapprove=(1,))
    await harness.start()
    old = await harness.decide()
    await eventually(lambda: harness.coordinator.records["case"].team_wait)
    record = harness.coordinator.records["case"]
    assert record.team_wait.plan_revision == 2
    assert len(harness.editors) == 1
    assert harness.workflow.cases["case"].session.approval is None
    assert (
        await harness.coordinator.consume_callback(
            old.model_copy(update={"update_id": 99, "callback_id": "stale"})
        )
    ).status == "stale"
    assert "1/3 fix attempts already used" in harness.messages[-1].text
    await harness.decide(update=2)
    final = await harness.finished()
    assert final.outcome.status == "completed" and final.attempt_count == 2
    assert [revision for revision, _ in harness.inputs] == [1, 2]


async def test_publication_uncertainty_reconciles_without_another_fix(tmp_path):
    harness = Harness(tmp_path)
    harness.publisher.publish_pending = 2
    await harness.start()
    await harness.decide()
    final = await harness.finished()
    assert final.outcome.status == "completed" and final.attempt_count == 1
    assert harness.publisher.publish_calls == 3
    assert len(harness.editors) == 1
    assert (
        sum(
            "publication transport is uncertain" in msg.text for msg in harness.messages
        )
        == 1
    )


async def test_early_ci_mailbox_and_stale_full_identity(tmp_path):
    harness = Harness(tmp_path)
    harness.hold_draft.clear()
    await harness.start()
    await harness.decide()
    await harness.draft_sending.wait()
    publication = harness.publisher.publications[0]
    event = CIObservation(
        publication=publication,
        status="passed",
        reason="early fixture completion",
        run_id=901,
        run_attempt=1,
    )
    for changes in (
        {"head_sha": "b" * 40},
        {"workflow_id": 99},
        {"run_id": "old-run"},
        {"candidate": publication.candidate.model_copy(update={"plan_revision": 2})},
        {"candidate": publication.candidate.model_copy(update={"digest": "b" * 64})},
    ):
        stale = event.model_copy(
            update={"publication": publication.model_copy(update=changes)}
        )
        assert (await harness.coordinator.consume_ci(stale)).status == "stale"
    assert (await harness.coordinator.consume_ci(event)).status == "accepted"
    assert (await harness.coordinator.consume_ci(event)).status == "duplicate"
    harness.hold_draft.set()
    assert (await harness.finished()).outcome.status == "completed"
    assert harness.publisher.publish_calls == 1
    assert (await harness.coordinator.consume_ci(event)).status == "stale"


async def test_pending_ci_holds_slot_without_spending_attempts_and_cancels(tmp_path):
    harness = Harness(tmp_path)
    harness.publisher.ci_status = "pending"
    await harness.start()
    await harness.decide()
    await eventually(lambda: harness.publisher.polls >= 3)
    assert len(harness.editors) == 1 and harness.publisher.ready_calls == 0
    assert (
        await harness.coordinator.accept_case(
            CaseRequest(
                source_thread_id="other",
                source_message_id="other",
                report="Other report",
            )
        )
    ).status == "busy"
    await harness.coordinator.close()
    polls = harness.publisher.polls
    await asyncio.sleep(0.03)
    assert harness.publisher.polls == polls
    assert harness.coordinator.active is None
    assert all(editor.closed for editor in harness.editors)


async def test_modified_evidence_after_ci_never_marks_ready(tmp_path):
    harness = Harness(tmp_path)
    harness.tamper_before = True
    await harness.start()
    await harness.decide()
    final = await harness.finished()
    assert final.outcome.status == "blocked"
    assert harness.publisher.publish_calls == 1 and harness.publisher.ready_calls == 0
    assert "no ready PR" in final.outcome.summary


async def test_readiness_race_does_not_claim_success(tmp_path):
    harness = Harness(tmp_path)
    harness.publisher.ready_status = "blocked"
    await harness.start()
    await harness.decide()
    final = await harness.finished()
    assert final.outcome.status == "blocked" and final.attempt_count == 1
    assert "CI is blocked" in final.outcome.summary


async def test_reject_never_creates_editing_or_publication_resources(tmp_path):
    harness = Harness(tmp_path)
    await harness.start()
    await harness.decide("reject")
    final = await harness.finished()
    assert final.phase == "rejected"
    assert not harness.editors and not harness.publisher.publications


async def test_missing_publisher_is_truthful_local_only_result(tmp_path):
    harness = Harness(tmp_path)
    harness.workflow.publisher = None
    await harness.start()
    await harness.decide()
    final = await harness.finished()
    assert final.outcome.status == "blocked"
    assert "Publication is disabled" in final.outcome.summary
    assert final.attempt_count == 1 and not harness.publisher.publications


async def test_forged_checkpoint_approval_cannot_mint_publication_authority(tmp_path):
    harness = Harness(tmp_path)
    await harness.start()
    record = harness.coordinator.records["case"]
    with pytest.raises(ValueError, match="coordinator approval"):
        await harness.coordinator.publication_authority(
            record.decision_case.model_copy(
                update={"phase": "verified", "attempt_count": 1}
            )
        )
    await harness.coordinator.close()


async def test_graph_with_real_publisher_git_export_and_ci_adapter(
    tmp_path: Path,
) -> None:
    git = await make_harness(tmp_path)
    harness = None
    try:
        base_sha = git.export.candidate.base_sha
        files = []
        for path in (
            "src/main.ts",
            "tests/smoke.spec.ts",
            "package.json",
            ".github/workflows/verify.yml",
        ):
            content = await git.runner.git(
                f"--git-dir={git.runner.remote}", "show", f"{base_sha}:{path}"
            )
            files.append(SourceFile(path=path, content=content.decode()))
        harness = Harness(
            tmp_path,
            base_sha=base_sha,
            base_files=tuple(files),
            source_path="src/main.ts",
        )
        harness.workflow.publisher = git.publisher
        harness.hold_draft.clear()
        await harness.start()
        await harness.decide()
        await asyncio.wait_for(harness.draft_sending.wait(), 5)
        publication = harness.coordinator.records["case"].ci_wait.publication
        assert git.runner.prs[0]["draft"]
        green(git, publication)
        harness.hold_draft.set()
        final = await harness.finished()
        assert final.outcome.status == "completed"
        assert final.publication.head_sha == await git.runner.head("fix/case")
        assert not git.runner.prs[0]["draft"]
        assert git.runner.create_count == 1
        committed = await git.runner.git(
            f"--git-dir={git.runner.remote}",
            "show",
            f"{publication.head_sha}:tests/regression.spec.ts",
        )
        assert committed == b"fixture UI behavior test"
        assert sum("push" in call.argv for call in git.runner.calls) == 1
    finally:
        if harness:
            await harness.coordinator.close()
        git.cli.close()
