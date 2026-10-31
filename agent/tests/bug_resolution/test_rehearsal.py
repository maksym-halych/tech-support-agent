"""Rehearsal audit never upgrades a graph acknowledgement into verified readiness."""

import argparse
import stat
from pathlib import Path

import pytest

from tech_support_agent.bug_resolution.contracts import (
    CaseRequest,
    CaseSnapshot,
    ExecutionOutcome,
)
from tech_support_agent.bug_resolution.rehearsal import (
    RehearsalManifest,
    local_url,
    main,
    record_case,
    save_manifest,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:3000",
        "http://localhost:3000",
        "http://user@127.0.0.1:3000",
        "http://127.0.0.1:3000/path",
        "http://127.0.0.1:3000?secret=value",
    ],
)
def test_remote_or_credential_endpoints_are_rejected(url: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        local_url(url)


@pytest.mark.parametrize(
    "phase,failed,expected",
    [
        ("finished", False, "blocked"),
        ("rejected", False, "rejected"),
        ("finished", True, "failed"),
    ],
)
def test_terminal_acknowledgement_is_not_ready(
    phase: str, failed: bool, expected: str
) -> None:
    manifest = RehearsalManifest(
        scenario="continue", run_id="run-1", thread_id="thread-1", base_sha="a" * 40
    )
    case = CaseSnapshot(
        run_id="run-1",
        case_id="case-1",
        request=CaseRequest(
            source_thread_id="thread-1",
            source_message_id="message-1",
            report="Continue is disabled",
        ),
        phase=phase,
        outcome=ExecutionOutcome(status="completed", summary="Not sufficient evidence"),
    )
    result = record_case(manifest, case, failed=failed)
    assert result.outcome == expected
    assert result.pr_url is None


def test_manifest_is_private_exclusive_and_roundtrips(tmp_path: Path) -> None:
    manifest = RehearsalManifest(
        scenario="both", run_id="run-1", thread_id="thread-1", base_sha="a" * 40
    )
    path = save_manifest(tmp_path, manifest)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert RehearsalManifest.model_validate_json(path.read_text()) == manifest
    with pytest.raises(FileExistsError):
        save_manifest(tmp_path, manifest)


def test_live_opt_in_is_required_before_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["rehearsal", "--scenario", "continue"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
