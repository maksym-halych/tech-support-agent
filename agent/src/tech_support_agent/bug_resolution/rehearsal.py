"""Run the real private runtime while a human drives a fresh public UI thread.

There are no scripted reports, patches, Telegram events, or success fixtures here.
An incomplete runtime or missing verification receipt is blocked, never ready.
"""

import argparse
import asyncio
import hashlib
import logging
import os
import signal
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, urlsplit

from aiohttp import ClientSession, web
from langgraph_sdk import get_client
from pydantic import Field, ValidationError

from tech_support_agent.bug_resolution.adapters.github import (
    GitHubAdapter,
    GitPublisher,
    HostCLI,
)
from tech_support_agent.bug_resolution.adapters.sandbox import (
    ImageArtifact,
    capture_profile,
)
from tech_support_agent.bug_resolution.adapters.telegram import TelegramClient
from tech_support_agent.bug_resolution.agents import create_bug_model
from tech_support_agent.bug_resolution.contracts import CaseSnapshot, Contract
from tech_support_agent.bug_resolution.coordinator import CaseCoordinator
from tech_support_agent.bug_resolution.preflight import (
    PublicationProbeSettings,
    investigation_preflight,
    publication_preflight,
)
from tech_support_agent.bug_resolution.publication import Publisher
from tech_support_agent.bug_resolution.publication_contracts import (
    GitHubCredentials,
    PublisherConfig,
    VerifiedExport,
)
from tech_support_agent.bug_resolution.runtime import Phase3Runtime
from tech_support_agent.bug_resolution.worker import create_app
from tech_support_agent.config import (
    BugSettings,
    DemoSettings,
    InvestigationSettings,
    PublicationSettings,
)

logger = logging.getLogger(__name__)


class ArtifactRecord(Contract):
    """An actual local file, not a model-provided evidence path."""

    path: str
    sha256: str
    size: int = Field(gt=0)


class RehearsalManifest(Contract):
    """Operator-only record. A completed graph alone does not prove a ready PR."""

    scenario: Literal["continue", "both"]
    run_id: str
    thread_id: str
    case_id: str | None = None
    base_sha: str
    plan_revision: int = 0
    attempts: int = 0
    phase: str = "awaiting_report"
    outcome: Literal[
        "pending", "blocked", "rejected", "failed", "interrupted", "ready"
    ] = "pending"
    reason: str = "Waiting for a real report and real human decisions."
    product_choice: str | None = None
    candidate_digest: str | None = None
    published_head: str | None = None
    pr_url: str | None = None
    ci_run: int | None = None
    evidence: list[ArtifactRecord] = Field(default_factory=list)
    cleanup_confirmed: bool = False


def local_url(value: str) -> str:
    """Reject credential-bearing or remote public/frontend endpoints."""

    url = urlsplit(value)
    if (
        url.scheme != "http"
        or url.hostname != "127.0.0.1"
        or not url.port
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
    ):
        raise argparse.ArgumentTypeError(
            "Use http://127.0.0.1:<port> without credentials or a path"
        )
    return value.rstrip("/")


def record_case(
    manifest: RehearsalManifest,
    case: CaseSnapshot,
    *,
    failed: bool,
    export: VerifiedExport | None = None,
) -> RehearsalManifest:
    """Record observed progress without treating a terminal acknowledgement as PR evidence."""

    outcome = (
        "failed" if failed else "rejected" if case.phase == "rejected" else "blocked"
    )
    reason = (
        "Private execution or cleanup failed; inspect retained evidence."
        if failed
        else "Human rejected the plan; no ready result claimed."
        if case.phase == "rejected"
        else "No complete candidate/publication/CI audit receipt is available; a terminal graph is not a successful rehearsal."
    )
    publication, ci = case.publication, case.ci_observation
    ready = bool(
        not failed
        and case.outcome
        and case.outcome.status == "completed"
        and export
        and publication
        and ci
        and ci.status == "passed"
        and ci.run_id
        and ci.publication == publication
        and publication.candidate == export.candidate
        and publication.run_id == case.run_id == manifest.run_id
        and export.candidate.case_id == case.case_id
        and export.candidate.base_sha == case.base_sha == manifest.base_sha
        and export.candidate.plan_revision == case.plan_revision
        and export.candidate.attempt == case.attempt_count
        and export.candidate.snapshot_digest
        and (
            case.scenario != "both"
            or (case.plan and case.plan.product_choice and case.discussion)
        )
    )
    return manifest.model_copy(
        update={
            "case_id": case.case_id,
            "plan_revision": case.plan_revision,
            "attempts": case.attempt_count,
            "phase": case.phase,
            "outcome": "ready" if ready else outcome,
            "reason": "Runtime confirmed ready PR with matching candidate and exact-head CI; no merge or deployment performed."
            if ready
            else reason,
            "candidate_digest": export.candidate.snapshot_digest if export else None,
            "published_head": publication.head_sha if publication else None,
            "pr_url": publication.pr_url if publication else None,
            "ci_run": ci.run_id if ci else None,
            "product_choice": case.plan.product_choice if case.plan else None,
        }
    )


def save_manifest(directory: Path, manifest: RehearsalManifest) -> Path:
    """Write a new private manifest exclusively; never overwrite prior evidence."""

    path = directory / f"rehearsal-{manifest.run_id}.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(manifest.model_dump_json(indent=2) + "\n")
    path.chmod(0o600)
    return path


async def rehearse(
    bug: BugSettings,
    investigation: InvestigationSettings,
    demo: DemoSettings,
    publication: PublicationSettings,
    *,
    public_url: str,
    frontend_url: str,
    timeout: float,
) -> RehearsalManifest:
    """Own a fresh real runtime; wait for UI input and Telegram polling, then clean up.

    The public Agent Server and authenticated Next.js UI are operator-owned.
    No client answer or team event is ever submitted by this runner.
    """

    profile = await capture_profile(demo, bug.scenario)
    artifacts: dict[str, ImageArtifact] = {}
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    manifest = None
    runtime = None
    coordinator = None
    runner = None
    poller = None
    cli = None
    cleanup_succeeded = False
    try:
        async with ClientSession() as session:
            telegram = TelegramClient(
                session,
                investigation.telegram_bot_token,
                investigation.telegram_chat_id,
                investigation.telegram_user_id,
                artifacts,
                investigation.artifacts_dir,
            )
            if not publication.publication_authorized:
                raise ValueError(
                    "Live rehearsal requires explicit publication authorization"
                )
            config = PublisherConfig(
                repo=profile.repo,
                base_branch=f"demo/{profile.scenario}",
                workflow_id=publication.github_workflow_id,
                live_authorized=True,
            )
            cli = HostCLI(config, GitHubCredentials(token=publication.github_token))
            publisher = Publisher(config, GitPublisher(cli), GitHubAdapter(cli))
            runtime = Phase3Runtime(
                investigation,
                profile,
                create_bug_model(investigation),
                telegram,
                artifacts,
                publisher=publisher,
                poll_interval=publication.ci_poll_seconds,
            )
            coordinator = CaseCoordinator(runtime.services())
            runner = web.AppRunner(create_app(bug, coordinator), access_log=None)
            try:
                await runner.setup()
                await web.TCPSite(
                    runner, "127.0.0.1", urlsplit(bug.worker_url).port
                ).start()
                client = get_client(url=public_url)
                thread_id = (await client.threads.create())["thread_id"]
                manifest = RehearsalManifest(
                    scenario=bug.scenario,
                    run_id=coordinator.run_id,
                    thread_id=thread_id,
                    base_sha=profile.base_sha,
                )
                poller = asyncio.create_task(
                    telegram.poll(
                        coordinator.consume_callback, coordinator.consume_reply
                    )
                )
                query = urlencode(
                    {
                        "apiUrl": public_url,
                        "assistantId": "bug_demo",
                        "threadId": thread_id,
                    }
                )
                logger.info(
                    "Open this fresh authenticated UI thread and submit the real bug report: %s/?%s",
                    frontend_url,
                    query,
                )
                logger.info(
                    "Only real Telegram button clicks authorize changes. Both requires a human product choice and a separate approval. Ctrl-C stops owned resources."
                )
                async with asyncio.timeout(timeout):
                    while not stop.is_set():
                        if poller.done():
                            raise RuntimeError("Telegram polling stopped")
                        case_id = coordinator.bindings.get(thread_id)
                        if case_id:
                            record = coordinator.records[case_id]
                            if record.terminal:
                                execution = runtime.workflow.cases.get(case_id)
                                manifest = record_case(
                                    manifest,
                                    record.final or record.initial,
                                    failed=record.failed,
                                    export=execution.export if execution else None,
                                )
                                break
                        await asyncio.sleep(0.25)
                    else:
                        manifest = manifest.model_copy(
                            update={
                                "outcome": "interrupted",
                                "reason": "Operator stopped the run; human silence is not approval.",
                            }
                        )
            except TimeoutError:
                if manifest:
                    manifest = manifest.model_copy(
                        update={
                            "outcome": "interrupted",
                            "reason": "Rehearsal time budget expired; pending human/CI work is not success.",
                        }
                    )
            finally:
                if poller:
                    poller.cancel()
                    await asyncio.gather(poller, return_exceptions=True)
                try:
                    await coordinator.close()
                finally:
                    try:
                        await runtime.close()
                    finally:
                        if runner:
                            await runner.cleanup()
                cleanup_succeeded = True
    finally:
        if cli:
            cli.close()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        if manifest:
            evidence = []
            for artifact in artifacts.values():
                path = artifact.path
                if (
                    path.parent == investigation.artifacts_dir
                    and path.is_file()
                    and not path.is_symlink()
                ):
                    data = path.read_bytes()
                    evidence.append(
                        ArtifactRecord(
                            path=str(path),
                            sha256=hashlib.sha256(data).hexdigest(),
                            size=len(data),
                        )
                    )
            clean = bool(
                cleanup_succeeded
                and coordinator
                and coordinator.closed
                and all(record.task.done() for record in coordinator.records.values())
                and runtime
                and not runtime.sandboxes
            )
            if not clean and manifest.outcome == "ready":
                manifest = manifest.model_copy(
                    update={
                        "outcome": "failed",
                        "reason": "Ready receipt observed but resource cleanup could not be confirmed.",
                    }
                )
            manifest = manifest.model_copy(
                update={"evidence": evidence, "cleanup_confirmed": clean}
            )
            path = save_manifest(investigation.artifacts_dir, manifest)
            logger.info(
                "Actual rehearsal manifest: %s; status=%s; cleanup=%s",
                path,
                manifest.outcome,
                clean,
            )
    if manifest is None:
        raise RuntimeError("Rehearsal did not create a public thread")
    return manifest


def main() -> None:
    """Require explicit live opt-in and confirmed target configuration before side effects."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, choices=["continue", "both"])
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument("--public-url", type=local_url, default="http://127.0.0.1:2024")
    parser.add_argument(
        "--frontend-url", type=local_url, default="http://127.0.0.1:3000"
    )
    parser.add_argument("--timeout", type=float, default=2400)
    args = parser.parse_args()
    if not args.run_live:
        parser.error(
            "Live rehearsal requires --run-live; no resources or messages were created"
        )
    if not 0 < args.timeout <= 86400:
        parser.error("Timeout must be between zero and 86400 seconds")
    logging.basicConfig(level=logging.INFO)
    try:
        bug = BugSettings(scenario=args.scenario)
        investigation = InvestigationSettings()
        demo = DemoSettings()
        publication = PublicationSettings()
        probe = PublicationProbeSettings()
    except ValidationError as error:
        fields = sorted(
            {
                "BUG_" + str(item["loc"][0]).upper()
                for item in error.errors(include_input=False)
            }
        )
        raise SystemExit(
            "Rehearsal blocked: missing/invalid configuration: " + ", ".join(fields)
        ) from None

    if not publication.publication_authorized:
        raise SystemExit(
            "Rehearsal blocked: BUG_PUBLICATION_AUTHORIZED=true is required for live publication"
        )

    async def run() -> RehearsalManifest:
        await publication_preflight(probe, demo)
        await investigation_preflight(investigation, demo)
        return await rehearse(
            bug,
            investigation,
            demo,
            publication,
            public_url=args.public_url,
            frontend_url=args.frontend_url,
            timeout=args.timeout,
        )

    try:
        manifest = asyncio.run(run())
    except Exception:
        raise SystemExit(
            "Rehearsal blocked by configuration, runtime, transport or cleanup failure; inspect retained evidence. No success claimed."
        ) from None
    raise SystemExit(
        0 if manifest.outcome == "ready" and manifest.cleanup_confirmed else 2
    )


if __name__ == "__main__":
    main()
