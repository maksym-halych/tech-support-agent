"""Exact workflow/PR/head/run-attempt/job checks and side-effect-free waits."""

from collections.abc import AsyncIterator
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from tech_support_agent.bug_resolution.nodes.publish import (
    PublicationState,
    route_ci,
    wait_for_ci,
)
from tech_support_agent.bug_resolution.publication_contracts import (
    CIObservation,
    PublicationError,
    PublicationRef,
)
from tests.bug_resolution.test_publication import Harness, make_harness


@pytest.fixture(name="harness")
async def ci_harness(tmp_path: Path) -> AsyncIterator[Harness]:
    value = await make_harness(tmp_path)
    try:
        yield value
    finally:
        value.cli.close()


def green(h: Harness, publication: PublicationRef) -> None:
    h.runner.run_rows = [
        {
            "id": 51,
            "workflow_id": 17,
            "path": ".github/workflows/verify.yml",
            "repository": {"full_name": publication.repo},
            "head_repository": {"full_name": publication.repo},
            "event": "pull_request",
            "head_sha": publication.head_sha,
            "head_branch": publication.branch,
            "run_number": 10,
            "run_attempt": 1,
            "status": "completed",
            "conclusion": "success",
            "pull_requests": [
                {
                    "number": publication.pr_number,
                    "head": {"sha": publication.head_sha},
                    "base": {"sha": publication.candidate.base_sha},
                }
            ],
        }
    ]
    h.runner.job_rows = [
        {
            "id": number,
            "run_id": 51,
            "run_attempt": 1,
            "head_sha": publication.head_sha,
            "name": name,
            "status": "completed",
            "conclusion": "success",
        }
        for number, name in enumerate(("tests", "build"), 1)
    ]


async def test_exact_success_marks_ready_without_attempt_changes(
    harness: Harness,
) -> None:
    h = harness
    publication = await h.publisher.publish(h.authority, h.export)
    green(h, publication)
    assert (await h.publisher.inspect_ci(publication)).status == "passed"
    observation = await h.publisher.mark_ready(h.authority, h.export, publication)
    assert observation.status == "passed" and not h.runner.prs[0]["draft"]
    assert h.authority.attempt_count == 1


@pytest.mark.parametrize(
    "update",
    [
        {"workflow_id": 18},
        {"path": ".github/workflows/other.yml"},
        {"head_sha": "a" * 40},
        {"event": "push"},
        {"event": "pull_request_target"},
        {"head_branch": "fix/foreign"},
        {"repository": {"full_name": "other/repo"}},
        {"head_repository": {"full_name": "other/repo"}},
        {"pull_requests": []},
        {"status": "queued", "conclusion": None},
        {"conclusion": "cancelled"},
        {"conclusion": "skipped"},
        {"conclusion": "neutral"},
    ],
)
async def test_wrong_or_incomplete_run_never_passes(
    harness: Harness, update: dict[str, Any]
) -> None:
    h = harness
    publication = await h.publisher.publish(h.authority, h.export)
    green(h, publication)
    h.runner.run_rows[0].update(update)
    assert (await h.publisher.inspect_ci(publication)).status == "pending"
    assert h.runner.prs[0]["draft"]


@pytest.mark.parametrize(
    "update",
    [
        {"conclusion": "skipped"},
        {"conclusion": "cancelled"},
        {"status": "in_progress"},
        {"run_attempt": 2},
        {"head_sha": "b" * 40},
        {"run_id": 99},
        {"name": "not-build"},
    ],
)
async def test_jobs_must_be_exact_completed_and_not_skipped(
    harness: Harness, update: dict[str, Any]
) -> None:
    h = harness
    publication = await h.publisher.publish(h.authority, h.export)
    green(h, publication)
    h.runner.job_rows[-1].update(update)
    assert (await h.publisher.inspect_ci(publication)).status == "pending"


async def test_absent_missing_duplicate_failed_and_transport_ci(
    harness: Harness,
) -> None:
    h = harness
    publication = await h.publisher.publish(h.authority, h.export)
    assert (await h.publisher.inspect_ci(publication)).status == "pending"
    green(h, publication)
    h.runner.job_rows.pop()
    assert (await h.publisher.inspect_ci(publication)).status == "pending"
    green(h, publication)
    h.runner.job_rows.append(deepcopy(h.runner.job_rows[0]))
    assert (await h.publisher.inspect_ci(publication)).status == "pending"
    green(h, publication)
    h.runner.run_rows[0]["conclusion"] = "failure"
    h.runner.job_rows[0]["conclusion"] = "failure"
    observation = await h.publisher.inspect_ci(publication)
    assert observation.status == "failed"
    assert (
        route_ci({"publication": publication, "ci_observation": observation})
        == "retry_or_handoff"
    )
    assert h.authority.attempt_count == 1
    h.runner.faults["GET pulls/1"] = "before"
    assert (await h.publisher.inspect_ci(publication)).status == "pending"


async def test_new_pending_run_and_rerun_override_old_green(harness: Harness) -> None:
    h = harness
    publication = await h.publisher.publish(h.authority, h.export)
    green(h, publication)
    newer = deepcopy(h.runner.run_rows[0])
    newer.update(id=52, run_number=11, status="queued", conclusion=None)
    h.runner.run_rows.append(newer)
    assert (await h.publisher.inspect_ci(publication)).status == "pending"
    green(h, publication)
    h.runner.run_rows[0]["run_attempt"] = 2
    assert (await h.publisher.inspect_ci(publication)).status == "pending"


async def test_merge_checkout_sha_requires_exact_parents_and_pr_head(
    harness: Harness,
) -> None:
    h = harness
    publication = await h.publisher.publish(h.authority, h.export)
    green(h, publication)
    merge = "e" * 40
    h.runner.prs[0]["merge_commit_sha"] = merge
    h.runner.parents[merge] = [publication.candidate.base_sha, publication.head_sha]
    for job in h.runner.job_rows:
        job["head_sha"] = merge
    assert (await h.publisher.inspect_ci(publication)).status == "passed"
    h.runner.run_rows[0]["head_sha"] = merge
    assert (await h.publisher.inspect_ci(publication)).status == "passed"
    h.runner.parents[merge] = [publication.candidate.base_sha, "f" * 40]
    assert (await h.publisher.inspect_ci(publication)).status == "pending"


async def test_wrong_pr_association_disabled_workflow_and_head_change(
    harness: Harness,
) -> None:
    h = harness
    publication = await h.publisher.publish(h.authority, h.export)
    green(h, publication)
    h.runner.run_rows[0]["pull_requests"][0]["number"] = 999
    assert (await h.publisher.inspect_ci(publication)).status == "pending"
    green(h, publication)
    h.runner.workflow_state = "disabled_manually"
    assert (await h.publisher.inspect_ci(publication)).status == "blocked"
    h.runner.workflow_state = "active"
    h.runner.ready_head_change = True
    assert (
        await h.publisher.mark_ready(h.authority, h.export, publication)
    ).status == "blocked"
    assert h.runner.prs[0]["draft"]


async def test_wait_interrupt_has_no_side_effect_and_rejects_stale_identity(
    harness: Harness,
) -> None:
    h = harness
    publication = await h.publisher.publish(h.authority, h.export)
    builder = StateGraph(PublicationState)
    builder.add_node("wait", wait_for_ci)
    builder.add_edge(START, "wait")
    builder.add_edge("wait", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "ci-wait"}}
    before = len(h.runner.calls)
    result = await graph.ainvoke({"publication": publication}, config)
    assert result["__interrupt__"]
    assert len(h.runner.calls) == before
    stale = publication.model_copy(update={"head_sha": "a" * 40})
    observation = CIObservation(
        publication=stale, status="passed", reason="stale", run_id=51, run_attempt=1
    )
    with pytest.raises(PublicationError, match="Stale"):
        await graph.ainvoke(Command(resume=observation), config)
    assert len(h.runner.calls) == before


async def test_stale_approval_cannot_mark_ready(harness: Harness) -> None:
    h = harness
    publication = await h.publisher.publish(h.authority, h.export)
    green(h, publication)
    with pytest.raises(PublicationError):
        await h.publisher.mark_ready(
            h.authority.model_copy(update={"approved_revision": 2}),
            h.export,
            publication,
        )
    assert h.runner.prs[0]["draft"]


async def test_pagination_does_not_hide_new_pending_run(harness: Harness) -> None:
    h = harness
    publication = await h.publisher.publish(h.authority, h.export)
    green(h, publication)
    old = h.runner.run_rows[0]
    h.runner.run_rows = [dict(old, id=n, run_number=n) for n in range(1, 101)]
    h.runner.run_rows.append(
        dict(old, id=101, run_number=101, status="queued", conclusion=None)
    )
    result = await h.publisher.inspect_ci(publication)
    assert result.status == "pending" and result.run_id == 101
    assert any("page=2" in arg for call in h.runner.calls for arg in call.argv)


async def test_json_resume_survives_checkpoint_and_attempt_three_stays_draft(
    harness: Harness,
) -> None:
    from tests.bug_resolution.test_publication import next_attempt

    h = harness
    export = next_attempt(h.export, 3)
    authority = h.authority.model_copy(update={"attempt_count": 3})
    publication = await h.publisher.publish(authority, export)
    green(h, publication)
    h.runner.run_rows[0]["conclusion"] = "failure"
    h.runner.job_rows[0]["conclusion"] = "failure"
    observation = await h.publisher.inspect_ci(publication)
    builder = StateGraph(PublicationState)
    builder.add_node("wait", wait_for_ci)
    builder.add_edge(START, "wait")
    builder.add_edge("wait", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "valid-ci"}}
    await graph.ainvoke({"publication": publication}, config)
    result = await graph.ainvoke(
        Command(resume=observation.model_dump(mode="json")), config
    )
    assert route_ci(result) == "retry_or_handoff"
    assert authority.attempt_count == 3
    assert h.runner.prs[0]["draft"]
