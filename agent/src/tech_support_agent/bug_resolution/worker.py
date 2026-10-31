"""Authenticated loopback API; the private graph has no HTTP resume surface."""

import json
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from urllib.parse import urlsplit

from aiohttp import web
from pydantic import ValidationError

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
from tech_support_agent.bug_resolution.graph import (
    IntakeServices,
    unavailable_delivery,
    unavailable_investigator,
)
from tech_support_agent.config import BugSettings


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


def main() -> None:
    """Start a loopback worker; unimplemented services fail closed, never succeed."""

    try:
        settings = BugSettings()
    except ValidationError:
        raise SystemExit(
            "Invalid bug-worker configuration; check BUG_WORKER_TOKEN and BUG_WORKER_URL"
        ) from None
    logging.basicConfig(level=logging.INFO)
    logging.getLogger(__name__).warning(
        "Investigator and team delivery are not wired; cases will fail closed"
    )
    coordinator = CaseCoordinator(
        IntakeServices(unavailable_investigator, unavailable_delivery)
    )
    web.run_app(
        create_app(settings, coordinator),
        host="127.0.0.1",
        port=urlsplit(settings.worker_url).port,
        access_log=None,
    )


if __name__ == "__main__":
    main()
