"""Docker-only execution with a persistent, bounded JSON-lines channel."""

import asyncio
import base64
import binascii
import io
import logging
import re
import tarfile
from pathlib import Path
from uuid import uuid4

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tech_support_agent.bug_resolution.adapters.commands import (
    CommandError,
    command_environment,
    run_command,
)
from tech_support_agent.bug_resolution.demo import ASSETS, TEMPLATE, Scenario
from tech_support_agent.bug_resolution.sandbox.protocol import (
    HARNESS_PATHS,
    MAX_FILE,
    MAX_LINE,
    MAX_PNG,
    CheckResult,
    Empty,
    FileContent,
    Operation,
    PageSnapshot,
    Prepare,
    Ready,
    Reply,
    Request,
    Result,
    RunCheck,
    Screenshot,
    SearchResult,
    SourceFile,
)
from tech_support_agent.config import DemoSettings

logger = logging.getLogger(__name__)


class SandboxError(RuntimeError):
    """Safe infrastructure/protocol failure, never evidence of an absent bug."""


class SandboxPolicyError(SandboxError):
    """A policy violation or stale browser reference rejected by the runner."""


class DemoProfile(BaseModel):
    """Operator-selected base captured before any demo code executes."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    repo: str
    directory: Path
    scenario: Scenario
    base_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    image: str


class ImageArtifact(BaseModel):
    """A decoded, canonical PNG exported to a trusted host artifact directory."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_id: str
    path: Path
    mime: str = "image/png"
    size: int
    width: int
    height: int


async def capture_profile(settings: DemoSettings, scenario: Scenario) -> DemoProfile:
    """Resolve only the configured local base branch, without fetch or checkout."""

    if scenario not in ("continue", "both"):
        raise ValueError("Unknown demo scenario")
    directory = settings.demo_repo_dir.resolve(strict=True)
    if directory.is_relative_to(ASSETS.parents[3].resolve()):
        raise SandboxPolicyError("Demo repository must be external")
    sha = await run_command(
        "git",
        "--no-replace-objects",
        "rev-parse",
        "--verify",
        f"refs/heads/demo/{scenario}^{{commit}}",
        cwd=directory,
    )
    return DemoProfile(
        repo=settings.demo_repo,
        directory=directory,
        scenario=scenario,
        base_sha=sha.decode("ascii").strip(),
        image=settings.sandbox_image,
    )


def archive_files(archive: bytes) -> list[SourceFile]:
    """Validate regular files and the fixed harness without extracting on the host."""

    if len(archive) > 2_000_000:
        raise SandboxPolicyError("Archive exceeds size budget")
    files = []
    seen: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
        for member in source:
            if member.isdir():
                continue
            if not member.isfile() or member.size > MAX_FILE or member.name in seen:
                raise SandboxPolicyError(
                    "Archive contains links, duplicates, or oversized files"
                )
            seen.add(member.name)
            if len(seen) > 100:
                raise SandboxPolicyError("Archive file count exceeded")
            content = source.extractfile(member)
            if content is None:
                raise SandboxPolicyError("Archive member missing")
            try:
                file = SourceFile(
                    path=member.name, content=content.read(MAX_FILE + 1).decode("utf-8")
                )
            except (ValueError, UnicodeError) as error:
                raise SandboxPolicyError("Archive path or encoding rejected") from error
            if (
                file.path in HARNESS_PATHS
                and file.content != (TEMPLATE / file.path).read_text()
            ):
                raise SandboxPolicyError("Prepared image and demo harness differ")
            files.append(file)
    if not HARNESS_PATHS.issubset(seen):
        raise SandboxPolicyError("Demo harness is incomplete")
    return files


def decode_reply(line: bytes, request: Request) -> Result:
    """Require one bounded response with a matching ID and operation result type."""

    if len(line) > MAX_LINE or not line.endswith(b"\n") or line.count(b"\n") != 1:
        raise SandboxError("Malformed runner response")
    try:
        reply = Reply.model_validate_json(line)
    except ValidationError as error:
        raise SandboxError("Malformed runner response") from error
    if reply.id != request.id:
        raise SandboxError("Runner response ID mismatch")
    if reply.error:
        if reply.error in ("policy", "stale_ref"):
            raise SandboxPolicyError(reply.error)
        raise SandboxError(reply.error)
    expected = {
        "prepare": Ready,
        "inspect": PageSnapshot,
        "navigate": PageSnapshot,
        "click": PageSnapshot,
        "select": PageSnapshot,
        "screenshot": Screenshot,
        "read_file": FileContent,
        "search_code": SearchResult,
        "check": CheckResult,
    }[request.command.op]
    if not isinstance(reply.result, expected):
        raise SandboxError("Unexpected runner result type")
    if (
        isinstance(request.command, RunCheck)
        and isinstance(reply.result, CheckResult)
        and reply.result.check != request.command.kind
    ):
        raise SandboxError("Check result mismatch")
    return reply.result


def export_png(screenshot: Screenshot, directory: Path) -> ImageArtifact:
    """Decode, constrain, and re-encode PNG bytes before an exclusive host write."""

    try:
        data = base64.b64decode(screenshot.data, validate=True)
        if len(data) > MAX_PNG or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("PNG size/type")
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or image.width * image.height > 4_000_000:
                raise ValueError("PNG dimensions/type")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            output = io.BytesIO()
            image.convert("RGB").save(output, format="PNG")
            width, height = image.size
        canonical = output.getvalue()
        if len(canonical) > MAX_PNG:
            raise ValueError("PNG size")
    except (OSError, ValueError, binascii.Error, Image.DecompressionBombError) as error:
        raise SandboxError("Invalid screenshot PNG") from error
    if not directory.is_dir() or any(
        p.is_symlink() for p in (directory, *directory.parents)
    ):
        raise SandboxPolicyError(
            "Artifact directory must be a trusted regular directory"
        )
    target = directory / f"{screenshot.artifact_id}.png"
    with target.open("xb") as output:
        output.write(canonical)
    return ImageArtifact(
        artifact_id=screenshot.artifact_id,
        path=target,
        size=len(canonical),
        width=width,
        height=height,
    )


class DockerSandbox:
    """One isolated case workspace; no shell, writes, or publication model tools."""

    def __init__(self) -> None:
        self.name = f"bug-sandbox-{uuid4().hex}"
        self.process: asyncio.subprocess.Process | None = None
        self.stderr_task: asyncio.Task[None] | None = None
        self.lock = asyncio.Lock()
        self.created = False
        self.closed = False
        self.url: str | None = None
        self.profile: DemoProfile | None = None
        self.initial_files: tuple[SourceFile, ...] = ()

    @classmethod
    async def prepare(
        cls, profile: DemoProfile, *, files: tuple[SourceFile, ...] | None = None
    ) -> DockerSandbox:
        """Export the captured base and start the trusted non-root runner in Docker."""

        sandbox = cls()
        sandbox.profile = profile
        try:
            if files is None:
                archive = await run_command(
                    "git",
                    "--no-replace-objects",
                    "archive",
                    "--format=tar",
                    profile.base_sha,
                    cwd=profile.directory,
                    max_output=2_000_000,
                )
                files = tuple(archive_files(archive))
            sandbox.initial_files = files
            image = (
                (
                    await run_command(
                        "docker",
                        "image",
                        "inspect",
                        "--format",
                        "{{.Id}}",
                        profile.image,
                    )
                )
                .decode()
                .strip()
            )
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
                raise SandboxError("Image must already exist locally")
            sandbox.profile = profile.model_copy(update={"image": image})
            # Capture the local image ID; never pull a mutable tag at case startup.
            sandbox.created = True
            await run_command(
                "docker",
                "create",
                "--name",
                sandbox.name,
                "--label",
                "bug-resolution.phase=2",
                "--interactive",
                "--init",
                "--user",
                "10001:10001",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--cpus",
                "2",
                "--memory",
                "1g",
                "--memory-swap",
                "1g",
                "--pids-limit",
                "256",
                "--shm-size",
                "128m",
                "--network",
                "bridge",
                "--tmpfs",
                "/work:rw,nosuid,nodev,size=512m,uid=10001,gid=10001,mode=0700",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=512m,uid=10001,gid=10001,mode=1777",
                "--publish",
                "127.0.0.1::5173",
                image,
            )
            sandbox.process = await asyncio.create_subprocess_exec(
                "docker",
                "start",
                "--attach",
                "--interactive",
                sandbox.name,
                env=command_environment(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_LINE + 1,
            )
            sandbox.stderr_task = asyncio.create_task(sandbox._discard_stderr())
            await sandbox.request(Prepare(files=list(files)), timeout=45)
            port = (
                (await run_command("docker", "port", sandbox.name, "5173/tcp"))
                .decode()
                .strip()
            )
            if not re.fullmatch(r"127\.0\.0\.1:[0-9]{1,5}", port):
                raise SandboxError("Demo port is not loopback-only")
            sandbox.url = f"http://{port}"
            return sandbox
        except BaseException:
            await sandbox.close()
            raise

    async def _discard_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        remaining = 32768
        while chunk := await self.process.stderr.read(65536):
            if remaining > 0:
                logger.debug(
                    f"Private runner stderr: {chunk[:remaining].decode('utf-8', errors='replace')}"
                )
                remaining -= len(chunk)

    async def request(self, command: Operation, *, timeout: float = 75) -> Result:
        """Send a validated operation; on timeout/protocol failure destroy the case."""

        request = Request(id=uuid4().hex, command=command)
        payload = request.model_dump_json(exclude_none=True).encode() + b"\n"
        if len(payload) > 1_000_000:
            raise SandboxPolicyError("Runner request exceeds size budget")
        async with self.lock:
            if (
                self.closed
                or self.process is None
                or self.process.returncode is not None
            ):
                raise SandboxError("Sandbox is not running")
            assert self.process.stdin is not None and self.process.stdout is not None
            try:
                async with asyncio.timeout(timeout):
                    self.process.stdin.write(payload)
                    await self.process.stdin.drain()
                    line = await self.process.stdout.readline()
                    return decode_reply(line, request)
            except SandboxPolicyError:
                raise
            except BaseException:
                await self.close()
                raise

    async def screenshot(self, directory: Path) -> ImageArtifact:
        """Capture a real screenshot and return only its validated artifact reference."""

        result = await self.request(Empty(op="screenshot"))
        assert isinstance(result, Screenshot)
        return export_png(result, directory)

    async def close(self) -> None:
        """Remove the owned container, including every descendant and writable layer."""

        if self.closed and not self.created:
            return
        self.closed = True
        try:
            if self.created:
                try:
                    await run_command("docker", "rm", "--force", "--volumes", self.name)
                except CommandError:
                    remaining = await run_command(
                        "docker",
                        "ps",
                        "--all",
                        "--quiet",
                        "--filter",
                        f"name=^{self.name}$",
                    )
                    if remaining.strip():
                        raise SandboxError(
                            f"Container cleanup requires retry: {self.name}"
                        ) from None
                self.created = False
        finally:
            if self.process is not None:
                if self.process.returncode is None:
                    self.process.kill()
                await self.process.wait()
            if self.stderr_task:
                self.stderr_task.cancel()
                await asyncio.gather(self.stderr_task, return_exceptions=True)


async def build_image(tag: str = "bug-resolution-sandbox:phase2") -> None:
    """Build trusted assets explicitly as an operator/test operation, never a tool."""

    if not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,100}", tag):
        raise ValueError("Invalid local image tag")
    await run_command(
        "docker",
        "build",
        "--tag",
        tag,
        "--file",
        "sandbox/Dockerfile",
        ".",
        cwd=ASSETS,
        timeout=600,
        max_output=5_000_000,
    )
