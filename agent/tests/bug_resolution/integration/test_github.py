"""Offline Git/gh boundary integration; live writes deliberately not provisioned."""

from pathlib import Path

from tests.bug_resolution.test_ci import green
from tests.bug_resolution.test_publication import make_harness, next_attempt


async def test_real_git_fake_gh_full_publish_ci_retry_ready(tmp_path: Path) -> None:
    """Exercise real commits/pushes only to a temporary local bare repository."""

    h = await make_harness(tmp_path)
    try:
        first = await h.publisher.publish(h.authority, h.export)
        green(h, first)
        h.runner.job_rows[0]["conclusion"] = "failure"
        h.runner.run_rows[0]["conclusion"] = "failure"
        assert (await h.publisher.inspect_ci(first)).status == "failed"
        export = next_attempt(h.export, 2)
        authority = h.authority.model_copy(update={"attempt_count": 2})
        second = await h.publisher.publish(authority, export, first)
        assert second.pr_number == first.pr_number
        assert (await h.publisher.inspect_ci(second)).status == "pending"
        green(h, second)
        assert (
            await h.publisher.mark_ready(authority, export, second)
        ).status == "passed"
        assert len(h.runner.prs) == 1 and not h.runner.prs[0]["draft"]
        assert authority.attempt_count == 2
    finally:
        h.cli.close()
