import base64
import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest
import yaml
from PIL import Image
from pydantic import ValidationError

from tech_support_agent.bug_resolution.adapters.commands import (
    CommandError,
    command_environment,
    run_command,
)
from tech_support_agent.bug_resolution.adapters.sandbox import (
    DockerSandbox,
    SandboxError,
    SandboxPolicyError,
    archive_files,
    decode_reply,
    export_png,
)
from tech_support_agent.bug_resolution.demo import ASSETS, demo_files, stage_demo
from tech_support_agent.bug_resolution.sandbox.protocol import (
    HARNESS_PATHS,
    CheckResult,
    Empty,
    Navigate,
    ReadFile,
    Request,
    RunCheck,
    Screenshot,
)
from tech_support_agent.bug_resolution.tools.browser import browser_tools
from tech_support_agent.bug_resolution.tools.files import file_tools
from tech_support_agent.config import DemoSettings


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../src/a.ts",
        "src/../a.ts",
        "src//a.ts",
        "src/./a.ts",
        "src/.env",
        "src/a.ts;id",
        "src/$(id).ts",
        "src\\a.ts",
        "src/a\x00.ts",
        "package.json",
        "pnpm-lock.yaml",
        ".git/config",
        ".github/workflows/verify.yml",
        "sandbox/runner.mjs",
        "tests/new.spec.ts",
        "src/node_modules/x.js",
        "src/node_modules/x.ts",
    ],
)
def test_protected_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        ReadFile(path=path)


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.invalid",
        "//evil.invalid",
        "/\\evil",
        "/%2f%2fevil",
        "/a/../b",
        "/?url=bad",
        "/#x",
        "/\nx",
        "javascript:alert(1)",
        "file:///etc/passwd",
    ],
)
def test_foreign_and_ambiguous_navigation(path: str) -> None:
    with pytest.raises(ValidationError):
        Navigate(path=path)


@pytest.mark.parametrize(
    "command",
    [
        {"op": "shell", "command": "id"},
        {"op": "check", "kind": "tests; id"},
        {"op": "check", "kind": "tests", "args": ["--update-snapshots"]},
        {"op": "inspect", "script": "process.env"},
        {"op": "screenshot", "path": "/tmp/x"},
        {"op": "write_file", "path": "src/a.ts", "content": "x"},
    ],
)
def test_no_shell_or_write_protocol(command: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Request.model_validate({"id": "a" * 32, "command": command})


@pytest.mark.parametrize(
    "line",
    [
        b"{}\n",
        b"null\n",
        b"not json\n",
        b"{}\n{}\n",
        b"{}",
        b"x" * 3_000_001,
        json.dumps({"id": "b" * 32, "result": {"kind": "ready"}}).encode() + b"\n",
        json.dumps({"id": "a" * 32, "result": {"kind": "ready"}}).encode() + b"\n",
        json.dumps(
            {"id": "a" * 32, "error": "policy", "result": {"kind": "ready"}}
        ).encode()
        + b"\n",
    ],
)
def test_malformed_or_mismatched_responses(line: bytes) -> None:
    request = Request(id="a" * 32, command=Empty(op="inspect"))
    with pytest.raises(SandboxError):
        decode_reply(line, request)


def make_archive(files: dict[str, str], link: bool = False) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for path, content in files.items():
            member = tarfile.TarInfo(path)
            data = content.encode()
            member.size = len(data)
            if link:
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc/passwd"
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def test_export_checks_harness_links_paths_and_size() -> None:
    files = demo_files("both")
    assert len(archive_files(make_archive(files))) == len(files)
    for extra in ({"../bad": "x"}, {"src/large.ts": "x" * 65537}, {".env": "x"}):
        with pytest.raises(SandboxPolicyError):
            archive_files(make_archive({**files, **extra}))
    with pytest.raises(SandboxPolicyError):
        archive_files(make_archive({"src/link.ts": ""}, link=True))
    with pytest.raises(SandboxPolicyError):
        archive_files(make_archive({**files, "package.json": "{}"}))
    with pytest.raises(SandboxPolicyError):
        archive_files(make_archive({"src/App.tsx": "x"}))


def test_generated_png_export_and_invalid_data(tmp_path: Path) -> None:
    image = io.BytesIO()
    Image.new("RGB", (20, 30), "green").save(image, format="PNG")
    screenshot = Screenshot(
        artifact_id="a" * 32,
        mime="image/png",
        data=base64.b64encode(image.getvalue()).decode(),
    )
    artifact = export_png(screenshot, tmp_path)
    assert artifact.path.name == "a" * 32 + ".png"
    assert (artifact.width, artifact.height) == (20, 30)
    with pytest.raises(FileExistsError):
        export_png(screenshot, tmp_path)
    for raw in (b"not png", image.getvalue()[:40]):
        with pytest.raises(SandboxError):
            export_png(
                screenshot.model_copy(update={"data": base64.b64encode(raw).decode()}),
                tmp_path,
            )
    with pytest.raises(SandboxError):
        export_png(screenshot.model_copy(update={"data": "!!!"}), tmp_path)
    with pytest.raises(ValidationError):
        Screenshot(artifact_id="../bad", mime="image/png", data="")
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(SandboxPolicyError):
        export_png(screenshot, link)


def test_coordinates_are_required_and_staging_never_initializes_git(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError):
        DemoSettings(_env_file=None)
    target = tmp_path / "demo"
    settings = DemoSettings(
        _env_file=None, demo_repo="fixture/quiz", demo_repo_dir=target
    )
    receipt = stage_demo(settings, "continue")
    assert receipt.files > 10
    assert not (target / ".git").exists()
    assert (target / "src/grade.ts").read_text() == demo_files("continue")[
        "src/grade.ts"
    ]
    with pytest.raises(ValueError, match="nonempty"):
        stage_demo(settings, "both")
    with pytest.raises(ValueError, match="outside"):
        stage_demo(
            settings.model_copy(update={"demo_repo_dir": ASSETS / "nested-demo"}),
            "both",
        )


def test_two_bases_share_harness_not_runtime_bug_flags() -> None:
    first, second = demo_files("continue"), demo_files("both")
    assert {key for key in first if first[key] != second[key]} == {
        "src/grade.ts",
        "src/subjects.ts",
    }
    assert all(first[key] == second[key] for key in HARNESS_PATHS)
    assert "regression" not in " ".join(first)
    assert "BUG_SCENARIO" not in "".join(first.values())


def test_ci_workflow_has_read_only_expected_jobs_and_pr_filters() -> None:
    # BaseLoader follows YAML 1.2's intended treatment of GitHub's `on` key.
    workflow = yaml.load(
        demo_files("both")[".github/workflows/verify.yml"], Loader=yaml.BaseLoader
    )
    assert workflow["on"] == {
        "pull_request": {"branches": ["demo/continue", "demo/both"]}
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"tests", "build"}
    for job in workflow["jobs"].values():
        assert job["timeout-minutes"] == "10"
        assert job["steps"][0]["with"]["persist-credentials"] == "false"
    text = json.dumps(workflow)
    assert "secrets" not in text and "pull_request_target" not in text
    assert "pnpm test" in text and "pnpm build" in text and "pnpm typecheck" in text


def test_private_tools_expose_no_execution_or_write_capability(tmp_path: Path) -> None:
    tools = browser_tools(DockerSandbox(), tmp_path) + file_tools(DockerSandbox())
    assert {tool.name for tool in tools} == {
        "inspect_page",
        "navigate",
        "click",
        "select",
        "screenshot",
        "read_file",
        "search_code",
    }


async def test_runner_policy_in_trusted_node_code() -> None:
    assert shutil.which("node"), "Node is required for trusted runner policy tests"
    output = await run_command(
        "node",
        "--test",
        "--test-reporter=tap",
        str(Path(__file__).with_name("sandbox_policy.test.mjs")),
    )
    assert b"# fail 0" in output


async def test_bounded_subprocess_and_secret_free_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-inherit")
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-inherit")
    assert not any("TOKEN" in key or "KEY" in key for key in command_environment())
    with pytest.raises(CommandError, match="output limit"):
        await run_command(
            "node", "-e", "process.stdout.write('x'.repeat(10000))", max_output=100
        )
    with pytest.raises(TimeoutError):
        await run_command("node", "-e", "setInterval(()=>{},1000)", timeout=0.1)


def test_wrong_check_identity_is_not_success() -> None:
    request = Request(id="a" * 32, command=RunCheck(kind="tests"))
    response = {
        "id": request.id,
        "result": {
            "kind": "check",
            "check": "build",
            "status": "passed",
            "exit_code": 0,
            "output": "",
        },
    }
    with pytest.raises(SandboxError, match="mismatch"):
        decode_reply(json.dumps(response).encode() + b"\n", request)


@pytest.mark.parametrize(
    "status,exit_code", [("passed", 1), ("passed", None), ("failed", 0), ("timeout", 0)]
)
def test_contradictory_check_result_is_rejected(
    status: str, exit_code: int | None
) -> None:
    with pytest.raises(ValidationError):
        CheckResult(check="tests", status=status, exit_code=exit_code, output="")


@pytest.mark.parametrize("payload", [b"{\n", b"{}\n", b"{", b"x" * 1_000_001])
async def test_malformed_input_terminates_real_runner_protocol(payload: bytes) -> None:
    with pytest.raises(CommandError):
        await run_command(
            "node", str(ASSETS / "sandbox/runner.mjs"), input_data=payload
        )


async def test_cleanup_can_retry_after_daemon_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = DockerSandbox()
    sandbox.created = True
    attempts = 0

    async def command(*args: str, **kwargs: object) -> bytes:
        nonlocal attempts
        if args[1] == "rm":
            attempts += 1
            if attempts == 1:
                raise CommandError("temporary daemon error")
            return b"removed"
        return b"container-id\n"

    monkeypatch.setattr(
        "tech_support_agent.bug_resolution.adapters.sandbox.run_command", command
    )
    with pytest.raises(SandboxError, match="retry"):
        await sandbox.close()
    assert sandbox.created
    await sandbox.close()
    assert sandbox.closed and not sandbox.created and attempts == 2
