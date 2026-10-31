"""Fixed Git/gh operations in an empty host environment, without a checkout."""

import asyncio
import base64
import json
import os
import re
import shutil
import signal
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tech_support_agent.bug_resolution.publication_contracts import (
    EvidenceExport,
    ExportFile,
    GitHubCredentials,
    PreparedCommit,
    PublicationError,
    PublicationPending,
    PublicationRef,
    PublisherConfig,
    PullRequest,
    Record,
    RemoteHeads,
    RunPullRequest,
    VerifiedExport,
    WorkflowIdentity,
    WorkflowJob,
    WorkflowRun,
    manifest_digest,
)
from tech_support_agent.bug_resolution.sandbox.protocol import HARNESS_PATHS


class Command(Record):
    """Internal process request, never exposed to the model or public server."""

    argv: tuple[str, ...]
    cwd: Path
    environment: dict[str, str] = Field(repr=False)
    stdin: bytes = Field(default=b"", repr=False)
    timeout: float = 60.0
    max_output: int = 8_000_000


class CommandOutput(Record):
    stdout: bytes = Field(repr=False)


class CommandRunner(Protocol):
    async def __call__(self, command: Command) -> CommandOutput: ...


async def execute(command: Command) -> CommandOutput:
    """Run fixed argv with bounded streams and kill the entire process group."""

    process = await asyncio.create_subprocess_exec(
        *command.argv,
        cwd=command.cwd,
        env=command.environment,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    used = 0

    async def drain(stream: asyncio.StreamReader) -> bytes:
        nonlocal used
        parts = []
        while chunk := await stream.read(65536):
            used += len(chunk)
            if used > command.max_output:
                raise PublicationPending("Publisher command output limit exceeded")
            parts.append(chunk)
        return b"".join(parts)

    async def send() -> None:
        assert process.stdin is not None
        process.stdin.write(command.stdin)
        await process.stdin.drain()
        process.stdin.close()

    assert process.stdout is not None and process.stderr is not None
    tasks = [
        asyncio.create_task(drain(process.stdout)),
        asyncio.create_task(drain(process.stderr)),
        asyncio.create_task(send()),
    ]
    try:
        async with asyncio.timeout(command.timeout):
            stdout, _, _ = await asyncio.gather(*tasks)
            if await process.wait() != 0:
                raise PublicationPending(
                    "Publisher command failed; reconcile remote state"
                )
            return CommandOutput(stdout=stdout)
    except (TimeoutError, OSError) as error:
        raise PublicationPending(
            "Publisher transport interrupted; reconcile remote state"
        ) from error
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await process.wait()


class HostCLI:
    """Own an empty CLI home, explicit token and fixed trusted executable paths."""

    def __init__(
        self,
        config: PublisherConfig,
        credentials: GitHubCredentials,
        *,
        runner: CommandRunner = execute,
    ) -> None:
        self.config = PublisherConfig.model_validate(config)
        self.credentials = GitHubCredentials.model_validate(credentials)
        self.runner = runner
        self._temporary = tempfile.TemporaryDirectory(prefix="bug-publisher-")
        self.directory = Path(self._temporary.name).resolve()
        self.git_executable = shutil.which("git") or "git"
        self.gh_executable = shutil.which("gh") or "gh"

    async def command(
        self,
        tool: str,
        args: tuple[str, ...],
        *,
        stdin: bytes = b"",
        dates: str | None = None,
    ) -> bytes:
        """Execute only adapter-generated Git/gh arguments with isolated settings."""

        if not self.config.live_authorized:
            raise PublicationError(
                "Configured demo repository and explicit live authorization required"
            )
        token = self.credentials.token.get_secret_value()
        if not token:
            raise PublicationError("Explicit host-only GitHub token required")
        environment = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.directory),
            "XDG_CONFIG_HOME": str(self.directory),
            "LANG": "C",
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
        if tool == "git":
            auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            environment.update(
                {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": f"http.https://github.com/{self.config.repo}.git.extraHeader",
                    "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {auth}",
                    "GIT_AUTHOR_NAME": "Bug resolution agent",
                    "GIT_COMMITTER_NAME": "Bug resolution agent",
                    "GIT_AUTHOR_EMAIL": "bug-resolution@users.noreply.github.com",
                    "GIT_COMMITTER_EMAIL": "bug-resolution@users.noreply.github.com",
                }
            )
            if dates:
                environment.update(GIT_AUTHOR_DATE=dates, GIT_COMMITTER_DATE=dates)
            argv = (
                self.git_executable,
                "--no-replace-objects",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "credential.helper=",
                "-c",
                "protocol.allow=never",
                "-c",
                "protocol.https.allow=always",
                "-c",
                "http.followRedirects=false",
                "-c",
                "core.attributesFile=/dev/null",
                *args,
            )
        elif tool == "gh":
            environment.update(
                GH_TOKEN=token,
                GH_HOST="github.com",
                GH_CONFIG_DIR=str(self.directory),
                GH_PROMPT_DISABLED="1",
                GH_PAGER="cat",
                GH_NO_UPDATE_NOTIFIER="1",
            )
            argv = (self.gh_executable, *args)
        else:
            raise PublicationError("Unsupported publisher executable")
        try:
            result = await self.runner(
                Command(
                    argv=argv, cwd=self.directory, environment=environment, stdin=stdin
                )
            )
        except (OSError, TimeoutError) as error:
            raise PublicationPending(
                "Publisher CLI unavailable or timed out"
            ) from error
        return CommandOutput.model_validate(result).stdout

    def close(self) -> None:
        """Remove only the owned temporary Git database and empty CLI home."""

        self._temporary.cleanup()


class Wire(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)


class Repository(Wire):
    full_name: str


class Ref(Wire):
    ref: str
    sha: str
    repo: Repository


class PRWire(Wire):
    number: int
    node_id: str
    base: Ref
    head: Ref
    merge_commit_sha: str | None
    state: str
    draft: bool
    body: str | None

    def normalized(self) -> PullRequest:
        return PullRequest(
            number=self.number,
            node_id=self.node_id,
            repo=self.base.repo.full_name,
            head_repo=self.head.repo.full_name,
            base_branch=self.base.ref,
            base_sha=self.base.sha,
            branch=self.head.ref,
            head_sha=self.head.sha,
            merge_sha=self.merge_commit_sha,
            state=self.state,
            draft=self.draft,
            body=self.body or "",
        )


class RunRef(Wire):
    sha: str


class RunPR(Wire):
    number: int
    head: RunRef
    base: RunRef


class RunWire(Wire):
    id: int
    workflow_id: int
    path: str
    repository: Repository
    head_repository: Repository
    event: str
    head_sha: str
    head_branch: str
    run_number: int
    run_attempt: int
    status: str
    conclusion: str | None
    pull_requests: list[RunPR]

    def normalized(self) -> WorkflowRun:
        return WorkflowRun(
            **self.model_dump(
                exclude={"repository", "head_repository", "pull_requests"}
            ),
            repo=self.repository.full_name,
            head_repo=self.head_repository.full_name,
            pull_requests=tuple(
                RunPullRequest(
                    number=pr.number, head_sha=pr.head.sha, base_sha=pr.base.sha
                )
                for pr in self.pull_requests
            ),
        )


class GitHubAdapter:
    """Explicit REST/CLI operations; never search for arbitrary green checks."""

    def __init__(self, cli: HostCLI) -> None:
        self.cli = cli
        self.config = cli.config

    async def _api(
        self, suffix: str, *, method: str = "GET", payload: dict[str, Any] | None = None
    ) -> Any:
        args = (
            "api",
            "--hostname",
            "github.com",
            "--method",
            method,
            f"repos/{self.config.repo}/{suffix}",
        )
        if payload is not None:
            args += ("--input", "-")
        output = await self.cli.command(
            "gh",
            args,
            stdin=json.dumps(payload).encode() if payload is not None else b"",
        )
        try:
            return json.loads(output)
        except (ValueError, UnicodeError) as error:
            raise PublicationPending("Malformed GitHub response") from error

    async def _pages(self, suffix: str, key: str | None = None) -> list[Any]:
        result = []
        separator = "&" if "?" in suffix else "?"
        for page in range(1, 11):
            response = await self._api(f"{suffix}{separator}per_page=100&page={page}")
            rows = response.get(key) if key and isinstance(response, dict) else response
            if not isinstance(rows, list):
                raise PublicationPending("Malformed GitHub pagination")
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise PublicationPending(
            "GitHub pagination budget exceeded; cannot prove complete result"
        )

    async def heads(self, branch: str) -> RemoteHeads:
        """Read exact remote refs; an empty fix ref is distinct from transport failure."""

        if not re.fullmatch(r"fix/[a-z0-9][a-z0-9-]{0,79}", branch):
            raise PublicationError("Invalid case branch")
        output = await self.cli.command(
            "git",
            (
                "ls-remote",
                "--refs",
                "--",
                f"https://github.com/{self.config.repo}.git",
                f"refs/heads/{self.config.base_branch}",
                f"refs/heads/{branch}",
            ),
        )
        refs = {}
        for line in output.decode("ascii").splitlines():
            sha, ref = line.split("\t", 1)
            if ref in refs:
                raise PublicationError("Ambiguous remote refs")
            refs[ref] = sha
        try:
            return RemoteHeads(
                base_sha=refs[f"refs/heads/{self.config.base_branch}"],
                branch_sha=refs.get(f"refs/heads/{branch}"),
            )
        except (KeyError, ValidationError) as error:
            raise PublicationError(
                "Configured remote base is missing or invalid"
            ) from error

    async def find_pr(self, branch: str) -> tuple[PullRequest, ...]:
        """Include closed PRs so replay cannot silently create a replacement."""

        if not re.fullmatch(r"fix/[a-z0-9][a-z0-9-]{0,79}", branch):
            raise PublicationError("Invalid case branch")
        rows = await self._pages(
            f"pulls?state=all&head={self.config.repo.split('/')[0]}:{branch}"
        )
        return tuple(PRWire.model_validate(row).normalized() for row in rows)

    async def get_pr(self, number: int) -> PullRequest:
        """Read current PR identity immediately before readiness decisions."""

        pr = PRWire.model_validate(await self._api(f"pulls/{number}")).normalized()
        if pr.number != number:
            raise PublicationError("GitHub returned a different PR number")
        return pr

    async def create_pr(self, branch: str, title: str, body: str) -> None:
        """Create one draft via explicit repo/head/base, with content on stdin."""

        await self._api(
            "pulls",
            method="POST",
            payload={
                "head": branch,
                "base": self.config.base_branch,
                "title": title,
                "body": body,
                "draft": True,
                "maintainer_can_modify": False,
            },
        )

    async def update_body(self, number: int, body: str) -> None:
        """Update only the report; never merge, close, or change the PR base."""

        await self._api(f"pulls/{number}", method="PATCH", payload={"body": body})

    async def set_ready(self, number: int, *, ready: bool) -> None:
        """Toggle draft state only; caller checks identity before and after."""

        args = ("pr", "ready", str(number), "--repo", self.config.repo)
        await self.cli.command("gh", args if ready else (*args, "--undo"))

    async def workflow(self) -> WorkflowIdentity:
        """Resolve the configured numeric workflow identity and expected path."""

        row = await self._api(f"actions/workflows/{self.config.workflow_id}")
        return WorkflowIdentity(id=row["id"], path=row["path"], state=row["state"])

    async def runs(self) -> tuple[WorkflowRun, ...]:
        """Read all bounded pages, including newer pending runs and reruns."""

        rows = await self._pages(
            f"actions/workflows/{self.config.workflow_id}/runs?event=pull_request",
            "workflow_runs",
        )
        return tuple(RunWire.model_validate(row).normalized() for row in rows)

    async def jobs(self, run: WorkflowRun) -> tuple[WorkflowJob, ...]:
        """Use the run-attempt endpoint, not jobs from a previous green attempt."""

        rows = await self._pages(
            f"actions/runs/{run.id}/attempts/{run.run_attempt}/jobs", "jobs"
        )
        return tuple(
            WorkflowJob.model_validate(
                {key: row[key] for key in WorkflowJob.model_fields}
            )
            for row in rows
        )

    async def commit_parents(self, sha: str) -> tuple[str, ...]:
        """Prove a synthetic merge checkout has the captured base and PR head."""

        if not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise PublicationError("Invalid merge SHA")
        row = await self._api(f"git/commits/{sha}")
        return tuple(parent["sha"] for parent in row["parents"])


class GitPublisher:
    """Bare Git plumbing avoids checkout, hooks, filters, scripts and symlinks."""

    def __init__(self, cli: HostCLI) -> None:
        self.cli = cli
        self.initialized = False
        self._dates: dict[str, str] = {}
        self._trees: dict[str, dict[str, tuple[str, str]]] = {}

    async def _git(
        self, *args: str, stdin: bytes = b"", dates: str | None = None
    ) -> bytes:
        return await self.cli.command(
            "git", ("--git-dir=objects.git", *args), stdin=stdin, dates=dates
        )

    async def _tree(self, sha: str) -> dict[str, tuple[str, str]]:
        tree = {}
        output = await self._git("ls-tree", "-r", "-z", "--full-tree", sha)
        for row in output.split(b"\0"):
            if not row:
                continue
            info, name = row.split(b"\t", 1)
            mode, kind, oid = info.decode("ascii").split(" ")
            path = name.decode("utf-8")
            if mode != "100644" or kind != "blob" or path in tree:
                raise PublicationError(
                    "Base/export contains non-regular files or links"
                )
            if (
                path.startswith("/")
                or "\\" in path
                or any(p in ("", ".", "..", ".git") for p in path.split("/"))
            ):
                raise PublicationError("Invalid Git tree path")
            tree[path] = (mode, oid)
        if len(tree) > 300:
            raise PublicationError("Git tree file budget exceeded")
        return tree

    async def _blob(self, oid: str) -> bytes:
        return await self._git("cat-file", "blob", oid)

    async def prepare(
        self,
        export: VerifiedExport,
        previous: PublicationRef | None,
        evidence: EvidenceExport,
    ) -> PreparedCommit:
        """Build a commit directly from literal blobs, with one stable retry identity."""

        if not self.initialized:
            await self.cli.command(
                "git", ("init", "--bare", "--template=", "--", "objects.git")
            )
            self.initialized = True
        config = self.cli.config
        remote = f"https://github.com/{config.repo}.git"
        await self._git(
            "fetch",
            "--no-tags",
            "--no-recurse-submodules",
            "--",
            remote,
            f"refs/heads/{config.base_branch}",
        )
        base = (await self._git("rev-parse", "FETCH_HEAD")).decode().strip()
        if base != export.candidate.base_sha:
            raise PublicationError("Base branch moved after investigation")
        tree = await self._tree(base)
        if any(
            path not in HARNESS_PATHS
            and path not in ("README.md", "LICENSE")
            and not path.startswith("src/")
            for path in tree
        ):
            raise PublicationError(
                "Base contains files outside the prepared demo allowlist"
            )
        source = {file.path: file.content for file in export.files}
        base_source = {
            path: await self._blob(oid)
            for path, (_, oid) in tree.items()
            if path.startswith(("src/", "tests/"))
        }
        for path, content in base_source.items():
            if path not in source or (
                path.startswith("tests/") and source[path] != content
            ):
                raise PublicationError(
                    "Candidate removed source or altered an existing test"
                )
        new_tests = {path for path in source if path.startswith("tests/")} - set(
            base_source
        )
        if new_tests != {export.regression_path}:
            raise PublicationError("Exactly one new regression test is required")
        # Validate the checked-in workflow, not a runtime-generated replacement.
        template = (
            Path(__file__).parents[1] / "demo_template/.github/workflows/verify.yml"
        )
        workflow = tree.get(config.workflow_path)
        if workflow is None or await self._blob(workflow[1]) != template.read_bytes():
            raise PublicationError(
                "Base workflow differs from the trusted read-only harness"
            )
        parent = base
        prefix = f"docs/evidence/{export.candidate.case_id}/"
        if previous:
            await self._git(
                "fetch",
                "--no-tags",
                "--no-recurse-submodules",
                "--",
                remote,
                f"refs/heads/{previous.branch}",
            )
            parent = (await self._git("rev-parse", "FETCH_HEAD")).decode().strip()
            if parent != previous.head_sha:
                raise PublicationError("Published branch changed externally")
            previous_tree = await self._tree(parent)
            tree.update(
                {
                    path: entry
                    for path, entry in previous_tree.items()
                    if path.startswith(prefix)
                }
            )
        await self._git("read-tree", base)
        for path, (mode, oid) in tree.items():
            if path.startswith(prefix):
                await self._git(
                    "update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}"
                )
        allowed_evidence = {
            f"{prefix}attempt-{export.candidate.attempt}-{kind}.png"
            for kind in ("before", "after")
        } | {f"{prefix}report.md"}
        evidence = EvidenceExport.model_validate(evidence)
        evidence_files = {file.path: file.content for file in evidence.files}
        if set(evidence_files) != allowed_evidence:
            raise PublicationError("Unexpected evidence export paths")
        for path, content in {**source, **evidence_files}.items():
            oid = (
                (await self._git("hash-object", "-w", "--stdin", stdin=content))
                .decode()
                .strip()
            )
            tree[path] = ("100644", oid)
            await self._git(
                "update-index", "--add", "--cacheinfo", f"100644,{oid},{path}"
            )
        tree_sha = (await self._git("write-tree")).decode().strip()
        key = f"{parent}:{tree_sha}:{export.candidate.model_dump_json()}"
        date = self._dates.setdefault(
            key, datetime.now(UTC).isoformat(timespec="seconds")
        )
        head = (
            (
                await self._git(
                    "commit-tree",
                    tree_sha,
                    "-p",
                    parent,
                    stdin=f"fix: verified case {export.candidate.case_id} attempt {export.candidate.attempt}\n".encode(),
                    dates=date,
                )
            )
            .decode()
            .strip()
        )
        commit = PreparedCommit(
            head_sha=head, parent_sha=parent, candidate=export.candidate
        )
        self._trees[head] = tree
        await self.verify(commit, export)
        return commit

    async def verify(self, commit: PreparedCommit, export: VerifiedExport) -> None:
        """Re-read committed bytes and the entire tree immediately before push."""

        if commit.candidate != export.candidate:
            raise PublicationError("Commit candidate mismatch")
        tree = await self._tree(commit.head_sha)
        if tree != self._trees.get(commit.head_sha):
            raise PublicationError("Committed tree changed or is not owned")
        files = tuple(
            [
                ExportFile(path=path, content=await self._blob(oid))
                for path, (_, oid) in tree.items()
                if path.startswith(("src/", "tests/"))
            ]
        )
        if manifest_digest(files) != export.candidate.digest:
            raise PublicationError(
                "Committed source/test digest differs from verification"
            )

    async def push(self, commit: PreparedCommit, branch: str) -> None:
        """Push an explicit SHA/ref normally; no force, merge or rebase operations."""

        if (
            branch != f"fix/{commit.candidate.case_id}"
            or commit.head_sha not in self._trees
        ):
            raise PublicationError("Cannot push an unowned commit/ref")
        await self._git(
            "push",
            "--porcelain",
            "--",
            f"https://github.com/{self.cli.config.repo}.git",
            f"{commit.head_sha}:refs/heads/{branch}",
        )
