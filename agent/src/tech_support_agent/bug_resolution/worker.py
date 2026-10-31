"""Authenticated loopback API; the private graph has no HTTP resume surface."""

import asyncio
import json
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from urllib.parse import urlsplit

from aiohttp import ClientSession, web
from pydantic import ValidationError

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
from tech_support_agent.bug_resolution.contracts import (
    MAX_BODY_BYTES,
    CaseRequest,
    ClarificationRequest,
    PublicEventQuery,
)
from tech_support_agent.bug_resolution.coordinator import (
    CaseCoordinator,
    CaseNotFound,
    CaseUnavailable,
)
from tech_support_agent.bug_resolution.preflight import check_model
from tech_support_agent.bug_resolution.publication import Publisher
from tech_support_agent.bug_resolution.publication_contracts import (
    GitHubCredentials,
    PublisherConfig,
)
from tech_support_agent.bug_resolution.runtime import Phase3Runtime
from tech_support_agent.config import (
    BugSettings,
    DemoSettings,
    InvestigationSettings,
    PublicationSettings,
)


def create_app(settings: BugSettings, coordinator: CaseCoordinator) -> web.Application:
    """Create only intake, public-event and typed clarification routes.

    Args:
        settings: Server-only token and bounded transport configuration.
        coordinator: Private execution owner, also shut down with the app.
    Returns:
        An application without CORS, state, history, approval or resume routes.
    """

    @web.middleware
    async def boundary(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        supplied = request.headers.get("Authorization", "").encode()
        expected = f"Bearer {settings.worker_token.get_secret_value()}".encode()
        if not secrets.compare_digest(supplied, expected):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            return await handler(request)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError:
            return web.json_response({"error": "invalid_request"}, status=400)
        except CaseNotFound:
            return web.json_response({"error": "not_found"}, status=404)
        except CaseUnavailable:
            return web.json_response({"error": "unavailable"}, status=503)
        except web.HTTPException as exc:
            return web.json_response({"error": "request_rejected"}, status=exc.status)
        except Exception:  # noqa: BLE001 - raw service errors must never reach HTTP
            logging.getLogger(__name__).error("Private API request failed")
            return web.json_response({"error": "unavailable"}, status=503)

    async def create_case(request: web.Request) -> web.Response:
        if request.content_type != "application/json" or request.query:
            raise ValueError("Expected JSON body only")
        body = CaseRequest.model_validate(await request.json())
        return web.json_response((await coordinator.accept_case(body)).model_dump())

    async def public_event(request: web.Request) -> web.Response:
        if len(request.query) != len(set(request.query)):
            raise ValueError("Duplicate query fields")
        query = PublicEventQuery.model_validate(dict(request.query))
        event = await coordinator.public_event(request.match_info["case_id"], query)
        return web.json_response(event.model_dump())

    async def clarification(request: web.Request) -> web.Response:
        if request.content_type != "application/json" or request.query:
            raise ValueError("Expected JSON body only")
        body = ClarificationRequest.model_validate(await request.json())
        receipt = await coordinator.clarify(request.match_info["case_id"], body)
        return web.json_response(receipt.model_dump())

    async def lifecycle(app: web.Application) -> AsyncIterator[None]:
        yield
        await coordinator.close()

    app = web.Application(middlewares=[boundary], client_max_size=MAX_BODY_BYTES)
    app.router.add_post("/cases", create_case)
    app.router.add_get("/cases/{case_id}/public-event", public_event)
    app.router.add_post("/cases/{case_id}/clarification", clarification)
    app.cleanup_ctx.append(lifecycle)
    return app


async def create_runtime_app(
    settings: BugSettings,
    investigation: InvestigationSettings,
    demo: DemoSettings,
    publication: PublicationSettings | None = None,
) -> web.Application:
    """Wire actual private services and shut polling down before resources are closed."""

    session = ClientSession()
    runtime = None
    cli = None
    try:
        publication = publication or PublicationSettings()
        profile = await capture_profile(demo, settings.scenario)
        model = create_bug_model(investigation)
        await check_model(model)
        artifacts: dict[str, ImageArtifact] = {}
        telegram = TelegramClient(
            session,
            investigation.telegram_bot_token,
            investigation.telegram_chat_id,
            investigation.telegram_user_id,
            artifacts,
            investigation.artifacts_dir,
        )
        await telegram.identity()
        publisher = None
        if publication.publication_authorized:
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
            model,
            telegram,
            artifacts,
            publisher=publisher,
            poll_interval=publication.ci_poll_seconds,
        )
        coordinator = CaseCoordinator(runtime.services())
        app = create_app(settings, coordinator)

        async def resources(app: web.Application) -> AsyncIterator[None]:
            poller = asyncio.create_task(
                telegram.poll(coordinator.consume_callback, coordinator.consume_reply)
            )
            try:
                yield
            finally:
                poller.cancel()
                await asyncio.gather(poller, return_exceptions=True)
                await coordinator.close()
                try:
                    await runtime.close()
                finally:
                    if cli:
                        cli.close()
                    await session.close()

        app.cleanup_ctx.append(resources)
        return app
    except BaseException:
        try:
            if runtime:
                await runtime.close()
        finally:
            if cli:
                cli.close()
            await session.close()
        raise


def main() -> None:
    """Start the real Phase 3 worker only with explicit configured private targets."""

    try:
        settings = BugSettings()
        investigation = InvestigationSettings()
        demo = DemoSettings()
        publication = PublicationSettings()
    except ValidationError:
        raise SystemExit(
            "Invalid bug-worker configuration; configure BUG worker, model, demo, artifact and Telegram settings"
        ) from None
    logging.basicConfig(level=logging.INFO)
    web.run_app(
        create_runtime_app(settings, investigation, demo, publication),
        host="127.0.0.1",
        port=urlsplit(settings.worker_url).port,
        access_log=None,
    )


if __name__ == "__main__":
    main()
