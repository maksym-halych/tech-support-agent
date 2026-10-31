"""Exclusive evidence writes with candidate provenance and content validation."""

import hashlib
from pathlib import Path
from uuid import uuid4

from tech_support_agent.bug_resolution.adapters.sandbox import export_png
from tech_support_agent.bug_resolution.fix_contracts import (
    CandidateRef,
    EvidenceRef,
    FixPolicyError,
)
from tech_support_agent.bug_resolution.sandbox.protocol import Screenshot


class EvidenceStore:
    """Worker-owned artifact directory; model outputs cannot register fake artifacts."""

    def __init__(self, directory: Path) -> None:
        if not directory.is_dir() or any(
            p.is_symlink() for p in (directory, *directory.parents)
        ):
            raise FixPolicyError(
                "Evidence directory must exist and contain no symlinks"
            )
        self.directory = directory
        self._records: dict[str, EvidenceRef] = {}

    def record_log(self, candidate: CandidateRef, text: str) -> EvidenceRef:
        """Persist bounded actual command output, never a model's pass assertion."""

        data = text.encode("utf-8")
        if len(data) > 200_000:
            raise FixPolicyError("Evidence log exceeds budget")
        data = data or b"(command produced no output)\n"
        artifact_id = uuid4().hex
        with (self.directory / f"{artifact_id}.log").open("xb") as output:
            output.write(data)
        return self._register(candidate, artifact_id, "log", data)

    def record_png(
        self, candidate: CandidateRef, screenshot: Screenshot
    ) -> EvidenceRef:
        """Decode and canonicalize an actual runner PNG before registering provenance."""

        artifact = export_png(screenshot, self.directory)
        return self._register(
            candidate, artifact.artifact_id, "png", artifact.path.read_bytes()
        )

    def _register(
        self, candidate: CandidateRef, artifact_id: str, kind: str, data: bytes
    ) -> EvidenceRef:
        ref = EvidenceRef(
            artifact_id=artifact_id,
            candidate=candidate,
            kind=kind,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
        )
        with (self.directory / f"{artifact_id}.json").open("x") as output:
            output.write(ref.model_dump_json())
        self._records[artifact_id] = ref
        return ref

    def validate(self, ref: EvidenceRef, candidate: CandidateRef) -> None:
        """Reject unknown, cross-candidate, replaced, or modified evidence files."""

        if ref.candidate != candidate or self._records.get(ref.artifact_id) != ref:
            raise FixPolicyError("Evidence provenance mismatch")
        path = self.directory / f"{ref.artifact_id}.{ref.kind}"
        if path.is_symlink() or not path.is_file():
            raise FixPolicyError("Evidence file replaced")
        data = path.read_bytes()
        if len(data) != ref.size or hashlib.sha256(data).hexdigest() != ref.sha256:
            raise FixPolicyError("Evidence content changed")
