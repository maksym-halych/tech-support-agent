import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image

from tech_support_agent.bug_resolution.adapters.commands import (
    CommandError,
    run_command,
)
from tech_support_agent.bug_resolution.adapters.sandbox import (
    DockerSandbox,
    SandboxPolicyError,
    build_image,
    capture_profile,
)
from tech_support_agent.bug_resolution.demo import Scenario, demo_files
from tech_support_agent.bug_resolution.sandbox.protocol import (
    CheckResult,
    Click,
    Empty,
    PageSnapshot,
    ReadFile,
    RunCheck,
    Select,
)
from tech_support_agent.config import DemoSettings

pytestmark = pytest.mark.docker


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def docker_image(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[str]:
    """Build against the local daemon without consulting personal registry helpers."""

    await run_command("docker", "version", "--format", "{{.Server.Version}}")
    endpoint = (
        os.environ.get("DOCKER_HOST")
        or (
            await run_command(
                "docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"
            )
        )
        .decode()
        .strip()
    )
    assert endpoint.startswith("unix://"), (
        "Docker tests require a local Unix-socket daemon"
    )
    directory = tmp_path_factory.mktemp("docker-cli-config")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DOCKER_CONFIG", str(directory))
        patch.setenv("DOCKER_HOST", endpoint)
        patch.delenv("DOCKER_CONTEXT", raising=False)
        await build_image()
        yield "bug-resolution-sandbox:phase2"


async def fixture_repository(directory: Path) -> None:
    """Create two independent temporary Git object fixtures, without hooks/remotes."""

    directory.mkdir()
    await run_command("git", "init", "--template=", str(directory))
    for scenario in ("continue", "both"):
        for relative, content in demo_files(scenario).items():
            blob = (
                (
                    await run_command(
                        "git",
                        "hash-object",
                        "-w",
                        "--stdin",
                        cwd=directory,
                        input_data=content.encode(),
                    )
                )
                .decode()
                .strip()
            )
            await run_command(
                "git",
                "update-index",
                "--add",
                "--cacheinfo",
                f"100644,{blob},{relative}",
                cwd=directory,
            )
        tree = (await run_command("git", "write-tree", cwd=directory)).decode().strip()
        commit = (
            (
                await run_command(
                    "git",
                    "-c",
                    "user.name=Isolated Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "commit-tree",
                    tree,
                    "-m",
                    f"Temporary {scenario} base",
                    cwd=directory,
                )
            )
            .decode()
            .strip()
        )
        await run_command(
            "git", "update-ref", f"refs/heads/demo/{scenario}", commit, cwd=directory
        )


def ref(page: PageSnapshot, name: str, role: str = "button") -> str:
    matches = [e for e in page.elements if e.role == role and e.name.startswith(name)]
    assert len(matches) == 1, (name, page)
    return matches[0].ref


async def page_result(sandbox: DockerSandbox) -> PageSnapshot:
    result = await sandbox.request(Empty(op="inspect"))
    assert isinstance(result, PageSnapshot)
    return result


async def click_named(sandbox: DockerSandbox, name: str) -> PageSnapshot:
    result = await sandbox.request(Click(ref=ref(await page_result(sandbox), name)))
    assert isinstance(result, PageSnapshot)
    return result


@pytest.mark.parametrize("scenario", ["continue", "both"])
async def test_independent_bases_browser_checks_and_isolation(
    docker_image: str,
    tmp_path: Path,
    scenario: Scenario,
) -> None:
    repository = tmp_path / "repository"
    await fixture_repository(repository)
    settings = DemoSettings(
        _env_file=None,
        demo_repo="isolated-fixture/quiz",
        demo_repo_dir=repository,
        sandbox_image=docker_image,
    )
    profile = await capture_profile(settings, scenario)
    sandbox = await DockerSandbox.prepare(profile)
    try:
        inspect = json.loads(await run_command("docker", "inspect", sandbox.name))[0]
        host, config = inspect["HostConfig"], inspect["Config"]
        assert config["User"] == "10001:10001"
        assert not host["Privileged"] and host["ReadonlyRootfs"]
        assert host["CapDrop"] == ["ALL"] and "no-new-privileges" in host["SecurityOpt"]
        assert host["NanoCpus"] == 2_000_000_000 and host["Memory"] == 1_073_741_824
        assert host["PidsLimit"] == 256 and host["NetworkMode"] == "bridge"
        assert not inspect["Mounts"] and not host["Binds"]
        assert set(host["Tmpfs"]) == {"/work", "/tmp"}
        assert sandbox.url and sandbox.url.startswith("http://127.0.0.1:")
        assert host["PortBindings"]["5173/tcp"][0]["HostIp"] == "127.0.0.1"
        assert not any(
            any(secret in value for secret in ("TOKEN=", "KEY=", "SECRET="))
            for value in config["Env"]
        )

        start = await page_result(sandbox)
        stale = ref(start, "Both")
        await sandbox.request(Click(ref=stale))
        with pytest.raises(SandboxPolicyError, match="stale_ref"):
            await sandbox.request(Click(ref=stale))
        page = await click_named(sandbox, "Continue")
        page = await sandbox.request(
            Select(ref=ref(page, "Grade", "combobox"), value="3")
        )
        assert isinstance(page, PageSnapshot)
        button = next(e for e in page.elements if e.name == "Continue")
        if scenario == "continue":
            assert button.disabled
            assert "Find your starting point" in page.text
            # This independence assertion stays outside the demo/fixer harness.
            output = await run_command(
                "docker",
                "exec",
                sandbox.name,
                "/usr/local/bin/node",
                "--input-type=module",
                "-e",
                "import assert from 'node:assert/strict'; import {summarySubjects} from '/work/project/src/subjects.ts'; assert.deepEqual(summarySubjects('Both'),['Math','Reading']);",
            )
            assert output == b""
        else:
            assert not button.disabled
            page = await sandbox.request(Click(ref=button.ref))
            assert isinstance(page, PageSnapshot) and "What would you like" in page.text
            page = await sandbox.request(
                Select(
                    ref=ref(page, "Learning goal", "combobox"), value="Build confidence"
                )
            )
            assert isinstance(page, PageSnapshot)
            page = await sandbox.request(Click(ref=ref(page, "See demo summary")))
            assert isinstance(page, PageSnapshot)
            assert (
                "Demo complete!" in page.text
                and "Math" in page.text
                and "Reading" not in page.text
            )
            assert "No enrollment or learning plan has been created" in page.text

        artifact = await sandbox.screenshot(tmp_path)
        with Image.open(artifact.path) as image:
            image.load()
            assert image.format == "PNG" and image.size == (1200, 850)
            assert len(image.getcolors(2_000_000) or []) > 100
        for kind in ("typecheck", "tests", "build"):
            result = await sandbox.request(RunCheck(kind=kind))
            assert isinstance(result, CheckResult)
            assert result.status == "passed", result.output

        content = await sandbox.request(ReadFile(path="src/grade.ts"))
        assert content.content == demo_files(scenario)["src/grade.ts"]
        # Test-only adversarial mutations run inside Docker, never on the host.
        await run_command(
            "docker",
            "exec",
            sandbox.name,
            "/usr/local/bin/node",
            "-e",
            "const fs=require('fs');fs.symlinkSync('/etc/passwd','/work/project/src/leak.ts');fs.writeFileSync('/work/previous-case','marker');",
        )
        with pytest.raises(SandboxPolicyError):
            await sandbox.request(ReadFile(path="src/leak.ts"))
    finally:
        await sandbox.close()
    assert not (
        await run_command(
            "docker", "ps", "--all", "--quiet", "--filter", f"name=^{sandbox.name}$"
        )
    ).strip()

    fresh = await DockerSandbox.prepare(profile)
    try:
        assert "What sparks your curiosity" in (await page_result(fresh)).text
        await run_command(
            "docker",
            "exec",
            fresh.name,
            "/usr/local/bin/node",
            "-e",
            "const fs=require('fs'); if(fs.existsSync('/work/previous-case')||fs.existsSync('/work/project/src/leak.ts'))process.exit(1);",
        )
    finally:
        await fresh.close()


async def test_runner_timeout_kills_descendants_and_adapter_timeout_removes_container(
    docker_image: str,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    await fixture_repository(repository)
    profile = await capture_profile(
        DemoSettings(
            _env_file=None,
            demo_repo="isolated-fixture/quiz",
            demo_repo_dir=repository,
            sandbox_image=docker_image,
        ),
        "both",
    )
    sandbox = await DockerSandbox.prepare(profile)
    try:
        script = """
import assert from 'node:assert/strict';
import {readFile, access} from 'node:fs/promises';
import {boundedProcess} from '/opt/runner/process.mjs';
const grandchild = "const fs=require('fs');fs.writeFileSync('/tmp/grandchild-pid',String(process.pid));setInterval(()=>{},1000)";
const parent = `require('child_process').spawn(process.execPath,['-e',${JSON.stringify(grandchild)}],{stdio:'ignore'});setInterval(()=>{},1000)`;
const result = await boundedProcess('/usr/local/bin/node',['-e',parent],{timeout:1000});
assert.equal(result.status,'timeout');
const pid = (await readFile('/tmp/grandchild-pid','utf8')).trim();
for(let i=0;i<50;i++){try{await access('/proc/'+pid)}catch{console.log('descendant removed');process.exit(0)}await new Promise(r=>setTimeout(r,20));}
throw new Error('grandchild survived');
"""
        assert b"descendant removed" in await run_command(
            "docker",
            "exec",
            sandbox.name,
            "/usr/local/bin/node",
            "--input-type=module",
            "-e",
            script,
        )
        with pytest.raises(TimeoutError):
            await sandbox.request(RunCheck(kind="tests"), timeout=0.001)
        assert sandbox.closed
        assert not (
            await run_command(
                "docker", "ps", "--all", "--quiet", "--filter", f"name=^{sandbox.name}$"
            )
        ).strip()
    finally:
        await sandbox.close()


async def test_browser_foreign_requests_and_redirects_are_blocked(
    docker_image: str, tmp_path: Path
) -> None:
    script = """
import assert from 'node:assert/strict';
import http from 'node:http';
import {Browser, ORIGIN} from '/opt/runner/browser.mjs';
let foreign = 0;
const server = http.createServer((req,res)=>{foreign++;res.end('foreign')});
await new Promise(resolve=>server.listen(5180,'127.0.0.1',resolve));
const demo = http.createServer((req,res)=>{
  if(req.url === '/redirect'){res.writeHead(302,{location:'http://127.0.0.1:5180/'});res.end();}
  else if(req.url === '/chain'){res.writeHead(302,{location:'/redirect'});res.end();}
  else {res.setHeader('Content-Type','text/html');res.end('<html><body><button>Safe demo</button></body></html>');}
});
await new Promise(resolve=>demo.listen(5173,'127.0.0.1',resolve));
const browser = new Browser();
try {
  await browser.start();
  await browser.page.evaluate(()=>fetch('http://127.0.0.1:5180/pixel').catch(()=>{}));
  for (const target of ['http://127.0.0.1:5180/', ORIGIN+'/redirect', ORIGIN+'/chain']) {
    const fresh = new Browser();
    try { await fresh.start(); await assert.rejects(fresh.page.goto(target)); }
    finally { await fresh.close(); }
  }
  assert.equal(foreign,0,'foreign origin received a request');
  console.log('foreign requests blocked');
} finally { await browser.close();server.close();demo.close(); }
"""
    output = await run_browser_script(docker_image, script)
    assert b"foreign requests blocked" in output


async def test_browser_refs_bind_visible_element_identities(docker_image: str) -> None:
    script = Path(__file__).with_name("browser_refs.mjs").read_text()
    output = await run_browser_script(docker_image, script)
    assert b"browser identity regressions passed" in output


async def run_browser_script(image: str, script: str) -> bytes:
    """Run trusted browser regressions only inside an isolated Docker container."""

    sandbox = DockerSandbox()
    try:
        await run_command(
            "docker",
            "create",
            "--name",
            sandbox.name,
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
            "--pids-limit",
            "256",
            "--network",
            "bridge",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=256m,uid=10001,gid=10001,mode=1777",
            "--entrypoint",
            "/usr/local/bin/node",
            image,
            "--input-type=module",
            "-e",
            script,
        )
        sandbox.created = True
        try:
            return await run_command(
                "docker",
                "start",
                "--attach",
                sandbox.name,
            )
        except CommandError as error:
            pytest.fail(error.diagnostic.decode())
    finally:
        await sandbox.close()
