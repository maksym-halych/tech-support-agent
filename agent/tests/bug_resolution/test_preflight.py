"""Preflight probes are read-only and fail closed on mismatched configuration."""

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tech_support_agent.bug_resolution import preflight
from tech_support_agent.config import DemoSettings


@pytest.mark.parametrize(
    "fault", [None, "base", "permission", "archived", "workflow", "content", "identity"]
)
async def test_publication_probe_checks_both_bases_without_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    calls = []
    closed = []
    template = (
        Path(preflight.__file__).with_name("demo_template")
        / ".github/workflows/verify.yml"
    )

    async def profile(demo, scenario):
        return SimpleNamespace(
            scenario=scenario, base_sha=("a" if scenario == "continue" else "b") * 40
        )

    class CLI:
        def __init__(self, config, credentials):
            self.config = config

        async def command(self, tool, args, **kwargs):
            calls.append((tool, args))
            sha = ("a" if self.config.base_branch == "demo/continue" else "b") * 40
            if tool == "git":
                assert args[0] == "ls-remote"
                return f"{'c' * 40 if fault == 'base' else sha}\trefs/heads/{self.config.base_branch}\n".encode()
            assert tool == "gh" and args[0] == "api"
            assert args[args.index("--method") + 1] == "GET"
            endpoint = args[-1]
            if "/contents/" in endpoint:
                result = {
                    "type": "file",
                    "encoding": "base64",
                    "content": base64.b64encode(
                        b"bad" if fault == "content" else template.read_bytes()
                    ).decode(),
                }
            elif "/runs?" in endpoint:
                result = {"workflow_runs": []}
            elif "/actions/workflows/" in endpoint:
                result = {
                    "id": 17,
                    "path": ".github/workflows/verify.yml",
                    "state": "disabled" if fault == "workflow" else "active",
                }
            else:
                result = {
                    "full_name": "wrong/repo" if fault == "identity" else "demo/quiz",
                    "archived": fault == "archived",
                    "permissions": {"push": fault != "permission"},
                }
            return json.dumps(result).encode()

        def close(self):
            closed.append(self.config.base_branch)

    monkeypatch.setattr(preflight, "capture_profile", profile)
    monkeypatch.setattr(preflight, "HostCLI", CLI)
    settings = preflight.PublicationProbeSettings(
        _env_file=None, github_token="test-only", github_workflow_id=17
    )
    demo = DemoSettings(_env_file=None, demo_repo="demo/quiz", demo_repo_dir=tmp_path)
    if fault:
        with pytest.raises(RuntimeError):
            await preflight.publication_preflight(settings, demo)
    else:
        await preflight.publication_preflight(settings, demo)
        assert closed == ["demo/continue", "demo/both"]
    assert closed and calls
    assert all(
        "push" not in args and "POST" not in args and "PATCH" not in args
        for _, args in calls
    )


def test_missing_configuration_has_no_side_effect_or_secret_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["preflight", "--stage", "all"])
    monkeypatch.setenv("BUG_DEMO_REPO", "secret-invalid-repository-value")
    with pytest.raises(SystemExit) as error:
        preflight.main()
    assert "BUG_DEMO_REPO" in str(error.value)
    assert "secret-invalid" not in str(error.value) + capsys.readouterr().out
