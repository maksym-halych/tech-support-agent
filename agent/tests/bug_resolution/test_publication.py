"""Real temporary Git databases and fake gh; no network, developer config or code."""

import asyncio
import io
import json
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image
from pydantic import SecretStr, ValidationError

from tech_support_agent.bug_resolution.adapters.github import (
    Command,
    CommandOutput,
    GitHubAdapter,
    GitPublisher,
    HostCLI,
    execute,
)
from tech_support_agent.bug_resolution.publication import Publisher, can_publish
from tech_support_agent.bug_resolution.publication_contracts import (
    CandidateIdentity,
    ExportFile,
    GitHubCredentials,
    LocalGate,
    PublicationAuthority,
    PublicationError,
    PublicationEvidence,
    PublicationPending,
    PublisherConfig,
    VerifiedExport,
    manifest_digest,
)

TEMPLATE = (
    Path(__file__).parents[2]
    / "src/tech_support_agent/bug_resolution/demo_template/.github/workflows/verify.yml"
)


class LocalRunner:
    """Replace only the exact configured remote URL and fake gh responses."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.remote = root / "remote.git"
        self.calls: list[Command] = []
        self.prs: list[dict[str, Any]] = []
        self.run_rows: list[dict[str, Any]] = []
        self.job_rows: list[dict[str, Any]] = []
        self.parents: dict[str, list[str]] = {}
        self.faults: dict[str, str] = {}
        self.ready_head_change = False
        self.workflow_state = "active"
        self.workflow_id = 17
        self.workflow_path = ".github/workflows/verify.yml"
        self.hide_created = False
        self.create_count = 0

    async def git(self, *args: str, stdin: bytes = b"") -> bytes:
        command = Command(
            argv=(
                "/usr/bin/git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                *args,
            ),
            cwd=self.root,
            environment={
                "PATH": "/usr/bin:/bin",
                "HOME": str(self.root),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_AUTHOR_NAME": "Test",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@example.invalid",
                "GIT_COMMITTER_EMAIL": "test@example.invalid",
            },
            stdin=stdin,
        )
        return (await execute(command)).stdout

    async def head(self, branch: str) -> str:
        return (
            (
                await self.git(
                    f"--git-dir={self.remote}", "rev-parse", f"refs/heads/{branch}"
                )
            )
            .decode()
            .strip()
        )

    async def _refresh(self, pr: dict[str, Any]) -> dict[str, Any]:
        pr["head"]["sha"] = await self.head(pr["head"]["ref"])
        pr["base"]["sha"] = await self.head(pr["base"]["ref"])
        return pr

    async def __call__(self, command: Command) -> CommandOutput:
        self.calls.append(command)
        argv = command.argv
        if Path(argv[0]).name == "git":
            operation = next(
                (name for name in ("commit-tree", "push", "fetch") if name in argv),
                "git",
            )
            fault = self.faults.pop(operation, None)
            if fault == "before":
                raise PublicationPending("Injected before operation")
            # No subprocess can contact GitHub: only this exact URL is rewritten.
            replaced = tuple(
                str(self.remote) if arg == "https://github.com/demo/quiz.git" else arg
                for arg in argv
            )
            assert not any(arg.startswith("https:") for arg in replaced)
            index = next(
                (
                    i
                    for i, arg in enumerate(replaced)
                    if arg in ("ls-remote", "fetch", "push")
                ),
                None,
            )
            if index is not None:
                replaced = (
                    *replaced[:index],
                    "-c",
                    "protocol.file.allow=always",
                    *replaced[index:],
                )
            result = await execute(command.model_copy(update={"argv": replaced}))
            if fault == "after":
                raise PublicationPending("Injected after operation")
            return result
        assert Path(argv[0]).name == "gh"
        if argv[1:3] == ("pr", "ready"):
            pr = self.prs[int(argv[3]) - 1]
            pr["draft"] = "--undo" in argv
            if self.ready_head_change and not pr["draft"]:
                await self.git(
                    f"--git-dir={self.remote}",
                    "update-ref",
                    f"refs/heads/{pr['head']['ref']}",
                    await self.head("demo/continue"),
                )
            return CommandOutput(stdout=b"")
        assert argv[1] == "api"
        method = argv[argv.index("--method") + 1]
        endpoint = argv[argv.index("--method") + 2].removeprefix("repos/demo/quiz/")
        page = int(parse_qs(urlsplit(endpoint).query).get("page", ["1"])[0])
        payload = json.loads(command.stdin) if command.stdin else None
        operation = f"{method} {endpoint.split('?')[0]}"
        fault = self.faults.pop(operation, None)
        if fault == "before":
            raise PublicationPending("Injected gh before operation")
        if endpoint.startswith("pulls?"):
            value = (
                []
                if self.hide_created
                else [await self._refresh(pr) for pr in self.prs]
            )
        elif endpoint == "pulls" and method == "POST":
            self.create_count += 1
            number = len(self.prs) + 1
            value = {
                "number": number,
                "node_id": f"PR_{number}",
                "state": "open",
                "draft": payload["draft"],
                "body": payload["body"],
                "merge_commit_sha": None,
                "base": {
                    "ref": payload["base"],
                    "sha": await self.head(payload["base"]),
                    "repo": {"full_name": "demo/quiz"},
                },
                "head": {
                    "ref": payload["head"],
                    "sha": await self.head(payload["head"]),
                    "repo": {"full_name": "demo/quiz"},
                },
            }
            self.prs.append(value)
        elif endpoint.startswith("pulls/"):
            value = await self._refresh(self.prs[int(endpoint.split("/")[1]) - 1])
            if method == "PATCH":
                value["body"] = payload["body"]
        elif endpoint.startswith("actions/workflows/") and "/runs?" not in endpoint:
            value = {
                "id": self.workflow_id,
                "path": self.workflow_path,
                "state": self.workflow_state,
            }
        elif "/runs?" in endpoint:
            value = {"workflow_runs": self.run_rows[(page - 1) * 100 : page * 100]}
        elif "/jobs?" in endpoint:
            value = {"jobs": self.job_rows[(page - 1) * 100 : page * 100]}
        elif endpoint.startswith("git/commits/"):
            value = {
                "parents": [
                    {"sha": sha} for sha in self.parents[endpoint.split("/")[-1]]
                ]
            }
        else:
            raise AssertionError(f"Unexpected fixed gh operation: {method} {endpoint}")
        if fault == "after":
            raise PublicationPending("Injected gh after operation")
        return CommandOutput(stdout=json.dumps(value).encode())


@dataclass
class Harness:
    runner: LocalRunner
    cli: HostCLI
    publisher: Publisher
    export: VerifiedExport
    authority: PublicationAuthority


async def make_harness(tmp_path: Path) -> Harness:
    runner = LocalRunner(tmp_path)
    await runner.git("init", "--bare", "--template=", str(runner.remote))
    base_files = {
        "src/main.ts": b"export const enabled = false;\n",
        "tests/smoke.spec.ts": b"// immutable baseline test\n",
        ".github/workflows/verify.yml": TEMPLATE.read_bytes(),
        "package.json": b'{"scripts":{"test":"do-not-execute-on-host"}}\n',
    }
    for path, data in base_files.items():
        oid = (
            (
                await runner.git(
                    f"--git-dir={runner.remote}",
                    "hash-object",
                    "-w",
                    "--stdin",
                    stdin=data,
                )
            )
            .decode()
            .strip()
        )
        await runner.git(
            f"--git-dir={runner.remote}",
            "update-index",
            "--add",
            "--cacheinfo",
            f"100644,{oid},{path}",
        )
    tree = (
        (await runner.git(f"--git-dir={runner.remote}", "write-tree")).decode().strip()
    )
    base = (
        (
            await runner.git(
                f"--git-dir={runner.remote}",
                "commit-tree",
                tree,
                stdin=b"fixture base\n",
            )
        )
        .decode()
        .strip()
    )
    for branch in ("demo/continue", "demo/both"):
        await runner.git(
            f"--git-dir={runner.remote}", "update-ref", f"refs/heads/{branch}", base
        )
    files = (
        ExportFile(path="src/main.ts", content=b"export const enabled = true;\n"),
        ExportFile(
            path="tests/smoke.spec.ts", content=base_files["tests/smoke.spec.ts"]
        ),
        ExportFile(
            path="tests/regression.spec.ts", content=b"// behavior-level test fixture\n"
        ),
    )
    candidate = CandidateIdentity(
        case_id="case-123",
        attempt=1,
        plan_revision=1,
        base_sha=base,
        digest=manifest_digest(files),
    )
    png = io.BytesIO()
    Image.new("RGB", (3, 3), "white").save(png, format="PNG")
    evidence = PublicationEvidence(
        candidate=candidate,
        before_png=png.getvalue(),
        after_png=png.getvalue(),
        problem="Continue blocked",
        cause="Incorrect disabled condition",
        approved_behavior="Continue navigates",
        changes="<script>alert(1)</script> ![x](https://evil.invalid) @everyone",
    )
    gates = tuple(
        LocalGate(
            candidate=candidate,
            kind=kind,
            outcome="expected_assertion_failure"
            if kind == "regression_on_base"
            else "passed",
            evidence_id=f"fixture-{kind}",
            summary=f"Offline fixture {kind}; not a real app verification",
        )
        for kind in ("regression_on_base", "tests", "build", "browser", "review")
    )
    export = VerifiedExport(
        candidate=candidate,
        files=files,
        regression_path="tests/regression.spec.ts",
        gates=gates,
        evidence=evidence,
    )
    authority = PublicationAuthority(
        run_id="run-1",
        case_id=candidate.case_id,
        attempt_count=1,
        plan_revision=1,
        approved_revision=1,
        approved_by=123,
        investigation="confirmed",
        phase="verified",
    )
    config = PublisherConfig(
        repo="demo/quiz",
        base_branch="demo/continue",
        workflow_id=17,
        live_authorized=True,
    )
    cli = HostCLI(
        config, GitHubCredentials(token=SecretStr("offline-test-only")), runner=runner
    )
    publisher = Publisher(config, GitPublisher(cli), GitHubAdapter(cli))
    return Harness(runner, cli, publisher, export, authority)


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    value = await make_harness(tmp_path)
    try:
        yield value
    finally:
        value.cli.close()


def next_attempt(export: VerifiedExport, attempt: int) -> VerifiedExport:
    files = tuple(
        file.model_copy(update={"content": file.content + b"// revision\n"})
        if file.path.startswith("src/")
        else file
        for file in export.files
    )
    candidate = export.candidate.model_copy(
        update={"attempt": attempt, "digest": manifest_digest(files)}
    )
    return export.model_copy(
        update={
            "candidate": candidate,
            "files": files,
            "gates": tuple(
                gate.model_copy(update={"candidate": candidate})
                for gate in export.gates
            ),
            "evidence": export.evidence.model_copy(update={"candidate": candidate}),
        }
    )


async def test_real_git_publication_and_replay(harness: Harness) -> None:
    h = harness
    publication = await h.publisher.publish(h.authority, h.export)
    assert publication.head_sha == await h.runner.head(publication.branch)
    assert h.runner.prs[0]["draft"]
    assert f"/blob/{publication.head_sha}/" in h.runner.prs[0]["body"]
    assert "<script>" not in h.runner.prs[0]["body"]
    assert "&#64;everyone" in h.runner.prs[0]["body"]
    assert not (h.cli.directory / "src").exists()
    assert await h.publisher.publish(h.authority, h.export, publication) == publication
    assert h.runner.create_count == 1
    assert sum("push" in call.argv for call in h.runner.calls) == 1
    assert all(
        "--force" not in call.argv
        and "merge" not in call.argv
        and "rebase" not in call.argv
        for call in h.runner.calls
    )
    assert all(
        "offline-test-only" not in " ".join(call.argv) for call in h.runner.calls
    )
    assert all("OPENAI_API_KEY" not in call.environment for call in h.runner.calls)


@pytest.mark.parametrize("operation", ["commit-tree", "push", "POST pulls"])
@pytest.mark.parametrize("when", ["before", "after"])
async def test_uncertain_operations_reconcile(
    harness: Harness, operation: str, when: str
) -> None:
    h = harness
    h.runner.faults[operation] = when
    try:
        publication = await h.publisher.publish(h.authority, h.export)
    except PublicationPending:
        if operation == "POST pulls" and when == "before":
            with pytest.raises(PublicationPending, match="uncertain"):
                await h.publisher.publish(h.authority, h.export)
            assert h.runner.create_count == 0
            return
        publication = await h.publisher.publish(h.authority, h.export)
    assert await h.runner.head(publication.branch) == publication.head_sha
    assert h.runner.create_count == 1
    assert len(h.runner.prs) == 1


async def test_second_candidate_same_pr_normal_descendant_preserves_evidence(
    harness: Harness,
) -> None:
    h = harness
    first = await h.publisher.publish(h.authority, h.export)
    export = next_attempt(h.export, 2)
    authority = h.authority.model_copy(update={"attempt_count": 2})
    second = await h.publisher.publish(authority, export, first)
    assert first.pr_number == second.pr_number
    assert h.runner.create_count == 1
    parent = (
        (
            await h.runner.git(
                f"--git-dir={h.runner.remote}", "rev-parse", f"{second.head_sha}^"
            )
        )
        .decode()
        .strip()
    )
    assert parent == first.head_sha
    paths = (
        await h.runner.git(
            f"--git-dir={h.runner.remote}",
            "ls-tree",
            "-r",
            "--name-only",
            second.head_sha,
        )
    ).decode()
    assert "attempt-1-before.png" in paths and "attempt-2-after.png" in paths
    assert authority.attempt_count == 2


@pytest.mark.parametrize(
    "update",
    [
        {"approved_revision": None},
        {"approved_revision": 2},
        {"approved_by": None},
        {"plan_revision": 2},
        {"attempt_count": 2},
        {"investigation": "unconfirmed"},
        {"investigation": "blocked"},
        {"phase": "rejected"},
        {"phase": "needs_approval"},
    ],
)
async def test_gate_rejects_before_any_command(
    harness: Harness, update: dict[str, Any]
) -> None:
    h = harness
    with pytest.raises(PublicationError):
        await h.publisher.publish(h.authority.model_copy(update=update), h.export)
    assert h.runner.calls == []


@pytest.mark.parametrize("cancel_during_check", [False, True])
async def test_cancelled_readiness_restores_changed_head(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, cancel_during_check: bool
) -> None:
    from tests.bug_resolution.test_ci import green

    h = harness
    publication = await h.publisher.publish(h.authority, h.export)
    green(h, publication)
    h.runner.ready_head_change = True
    mutated = asyncio.Event()
    restoring = asyncio.Event()
    release = asyncio.Event()
    original = h.publisher.github.set_ready
    inspect = h.publisher.inspect_ci

    async def set_ready(number: int, *, ready: bool) -> None:
        await original(number, ready=ready)
        if ready:
            mutated.set()
            if not cancel_during_check:
                await asyncio.Future()
        else:
            restoring.set()
            await release.wait()
            raise PublicationPending("Lost draft acknowledgement")

    async def inspect_ci(ref: Any) -> Any:
        if mutated.is_set():
            await asyncio.Future()
        return await inspect(ref)

    monkeypatch.setattr(h.publisher.github, "set_ready", set_ready)
    monkeypatch.setattr(h.publisher, "inspect_ci", inspect_ci)
    task = asyncio.create_task(
        h.publisher.mark_ready(h.authority, h.export, publication)
    )
    await asyncio.wait_for(mutated.wait(), 2)
    assert not h.runner.prs[0]["draft"]
    task.cancel()
    await asyncio.wait_for(restoring.wait(), 2)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert h.runner.prs[0]["draft"]
    assert h.runner.prs[0]["head"]["sha"] == publication.candidate.base_sha
    assert h.authority.attempt_count == 1
    assert not h.publisher._lock.locked()


@pytest.mark.parametrize("gate_index", range(5))
async def test_every_gate_required_and_candidate_bound(
    harness: Harness, gate_index: int
) -> None:
    h = harness
    gates = list(h.export.gates)
    gates[gate_index] = gates[gate_index].model_copy(update={"outcome": "blocked"})
    with pytest.raises(PublicationError):
        await h.publisher.publish(
            h.authority, h.export.model_copy(update={"gates": tuple(gates)})
        )
    assert h.runner.calls == []


async def test_mutation_missing_gate_bad_png_and_attempt_four_fail_closed(
    harness: Harness,
) -> None:
    h = harness
    mutations = [
        h.export.model_copy(
            update={
                "files": (
                    h.export.files[0].model_copy(update={"content": b"changed"}),
                    *h.export.files[1:],
                )
            }
        ),
        h.export.model_copy(update={"gates": h.export.gates[:-1]}),
        h.export.model_copy(
            update={
                "evidence": h.export.evidence.model_copy(
                    update={"after_png": b"not png"}
                )
            }
        ),
        next_attempt(h.export, 4),
    ]
    for export in mutations:
        with pytest.raises((PublicationError, ValidationError)):
            await h.publisher.publish(h.authority, export)
    assert h.runner.calls == []


@pytest.mark.parametrize(
    "path",
    [
        "../src/main.ts",
        "/src/main.ts",
        "src/.git/config.ts",
        "src/x/../../evil.ts",
        ".github/workflows/verify.yml",
        "package.json",
        "tests/old.test.js",
        "src/link",
        "src/node_modules/a.ts",
    ],
)
def test_forbidden_export_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        ExportFile(path=path, content=b"bad")


async def test_existing_tests_cannot_change(harness: Harness) -> None:
    h = harness
    files = tuple(
        file.model_copy(update={"content": b"disabled"})
        if file.path == "tests/smoke.spec.ts"
        else file
        for file in h.export.files
    )
    candidate = h.export.candidate.model_copy(update={"digest": manifest_digest(files)})
    export = h.export.model_copy(
        update={
            "files": files,
            "candidate": candidate,
            "gates": tuple(
                gate.model_copy(update={"candidate": candidate})
                for gate in h.export.gates
            ),
            "evidence": h.export.evidence.model_copy(update={"candidate": candidate}),
        }
    )
    with pytest.raises(PublicationError, match="existing test"):
        await h.publisher.publish(h.authority, export)
    assert not any("push" in call.argv for call in h.runner.calls)


@pytest.mark.parametrize("branch", ["demo/continue", "fix/case-123"])
async def test_external_branch_movement_blocks(harness: Harness, branch: str) -> None:
    h = harness
    first = await h.publisher.publish(h.authority, h.export)
    await h.runner.git(
        f"--git-dir={h.runner.remote}",
        "update-ref",
        f"refs/heads/{branch}",
        first.head_sha if branch.startswith("demo") else first.candidate.base_sha,
    )
    with pytest.raises(PublicationError, match="branch"):
        await h.publisher.publish(h.authority, h.export)
    assert sum("push" in call.argv for call in h.runner.calls) == 1


async def test_no_live_authorization_means_zero_commands(harness: Harness) -> None:
    h = harness
    h.publisher.config = h.publisher.config.model_copy(
        update={"live_authorized": False}
    )
    with pytest.raises(PublicationError, match="authorization"):
        await h.publisher.publish(h.authority, h.export)
    assert h.runner.calls == []


async def test_export_digest_is_order_independent_and_framed(harness: Harness) -> None:
    assert manifest_digest(harness.export.files) == manifest_digest(
        tuple(reversed(harness.export.files))
    )
    with pytest.raises(ValueError, match="duplicate"):
        manifest_digest((harness.export.files[0], harness.export.files[0]))
    can_publish(harness.authority, harness.export)


async def test_hostile_home_hooks_and_filters_are_never_executed(
    harness: Harness,
) -> None:
    h = harness
    marker = h.runner.root / "executed"
    hooks = h.runner.root / "hostile-hooks"
    hooks.mkdir()
    script = f'#!/bin/sh\ntouch "{marker}"\nexit 1\n'
    for name in ("pre-push", "pre-commit", "post-checkout"):
        path = hooks / name
        path.write_text(script)
        path.chmod(0o700)
    (h.cli.directory / ".gitconfig").write_text(
        f"[core]\n hooksPath = {hooks}\n[credential]\n helper = !touch {marker}\n[commit]\n gpgSign = true\n"
    )
    publication = await h.publisher.publish(h.authority, h.export)
    assert publication.head_sha == await h.runner.head(publication.branch)
    assert not marker.exists()
    assert not (h.cli.directory / "objects.git/hooks").exists()


@pytest.mark.parametrize("mode,path", [("120000", "src/link.ts"), ("100644", ".env")])
async def test_link_or_secret_in_base_blocks_publication(
    harness: Harness, mode: str, path: str
) -> None:
    h = harness
    git_dir = f"--git-dir={h.runner.remote}"
    oid = (
        (
            await h.runner.git(
                git_dir, "hash-object", "-w", "--stdin", stdin=b"forbidden"
            )
        )
        .decode()
        .strip()
    )
    await h.runner.git(
        git_dir, "update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}"
    )
    tree = (await h.runner.git(git_dir, "write-tree")).decode().strip()
    base = (
        (
            await h.runner.git(
                git_dir,
                "commit-tree",
                tree,
                "-p",
                h.export.candidate.base_sha,
                stdin=b"hostile base\n",
            )
        )
        .decode()
        .strip()
    )
    await h.runner.git(git_dir, "update-ref", "refs/heads/demo/continue", base)
    candidate = h.export.candidate.model_copy(update={"base_sha": base})
    export = h.export.model_copy(
        update={
            "candidate": candidate,
            "gates": tuple(
                gate.model_copy(update={"candidate": candidate})
                for gate in h.export.gates
            ),
            "evidence": h.export.evidence.model_copy(update={"candidate": candidate}),
        }
    )
    with pytest.raises(PublicationError):
        await h.publisher.publish(h.authority, export)
    assert not any("push" in call.argv for call in h.runner.calls)


async def test_duplicate_closed_or_relabelled_pr_blocks_replay(
    harness: Harness,
) -> None:
    h = harness
    await h.publisher.publish(h.authority, h.export)
    pr = h.runner.prs[0]
    pr["state"] = "closed"
    with pytest.raises(PublicationError):
        await h.publisher.publish(h.authority, h.export)
    pr["state"] = "open"
    body = pr["body"]
    pr["body"] = "foreign PR"
    with pytest.raises(PublicationError):
        await h.publisher.publish(h.authority, h.export)
    pr["body"] = body
    h.runner.prs.append(pr.copy())
    with pytest.raises(PublicationError, match="Multiple"):
        await h.publisher.publish(h.authority, h.export)
    assert h.runner.create_count == 1


async def test_invisible_create_is_not_duplicated(harness: Harness) -> None:
    h = harness
    h.runner.hide_created = True
    h.runner.faults["POST pulls"] = "after"
    for _ in range(2):
        with pytest.raises(PublicationPending):
            await h.publisher.publish(h.authority, h.export)
    assert h.runner.create_count == 1
    h.runner.hide_created = False
    assert (await h.publisher.publish(h.authority, h.export)).pr_number == 1


@pytest.mark.parametrize("when", ["before", "after"])
async def test_report_update_timeout_reconciles_same_commit(
    harness: Harness, when: str
) -> None:
    h = harness
    first = await h.publisher.publish(h.authority, h.export)
    export = next_attempt(h.export, 2)
    authority = h.authority.model_copy(update={"attempt_count": 2})
    h.runner.faults["PATCH pulls/1"] = when
    try:
        second = await h.publisher.publish(authority, export, first)
    except PublicationPending:
        second = await h.publisher.publish(authority, export, first)
    assert second.pr_number == first.pr_number
    assert second.head_sha in h.runner.prs[0]["body"]
    assert sum("push" in call.argv for call in h.runner.calls) == 2


async def test_bounded_executor_kills_children_on_timeout(tmp_path: Path) -> None:
    marker = tmp_path / "child-survived"
    child = f"import time; from pathlib import Path; time.sleep(0.6); Path({str(marker)!r}).write_text('bad')"
    parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(10)"
    command = Command(
        argv=(sys.executable, "-c", parent),
        cwd=tmp_path,
        environment={"PATH": "/usr/bin:/bin"},
        timeout=0.2,
    )
    with pytest.raises(PublicationPending, match="interrupted"):
        await execute(command)
    await asyncio.sleep(0.7)
    assert not marker.exists()


async def test_executor_output_bound(tmp_path: Path) -> None:
    command = Command(
        argv=(sys.executable, "-c", "import sys; sys.stdout.write('x'*10000)"),
        cwd=tmp_path,
        environment={"PATH": "/usr/bin:/bin"},
        max_output=100,
    )
    with pytest.raises(PublicationPending, match="output limit"):
        await execute(command)


async def test_report_budget_blocks_before_remote_work(harness: Harness) -> None:
    h = harness
    evidence = h.export.evidence.model_copy(
        update={"problem": "&" * 8000, "cause": "&" * 8000}
    )
    with pytest.raises(PublicationError, match="body budget"):
        await h.publisher.publish(
            h.authority, h.export.model_copy(update={"evidence": evidence})
        )
    assert h.runner.calls == []
