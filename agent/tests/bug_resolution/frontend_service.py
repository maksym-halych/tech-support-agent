"""Test-only loopback harness. Never imported by either production entrypoint."""

import asyncio
import signal
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestServer
from pydantic import Field

from tech_support_agent.bug_resolution.adapters.private_api import PrivateAPI
from tech_support_agent.bug_resolution.contracts import Contract
from tech_support_agent.bug_resolution.coordinator import CaseCoordinator
from tech_support_agent.bug_resolution.worker import create_app
from tech_support_agent.config import BugSettings
from tests.bug_resolution.conftest import TOKEN, FakeServices
from tests.bug_resolution.integration.test_public_server import server as server_fixture

CONTROL_TOKEN = "phase6-test-control-only-not-production"


class ResetRequest(Contract):
    question: str | None = "Which grade did you select?"
    fail_delivery: bool = Field(default=False, strict=True)


async def serve() -> None:
    """Own fake worker services and an actual isolated Agent Server child process."""

    settings = BugSettings(
        _env_file=None, worker_token=TOKEN, worker_url="http://127.0.0.1:2028"
    )
    services = FakeServices()
    coordinator = CaseCoordinator(services.dependencies())
    worker = TestServer(create_app(settings, coordinator), host="127.0.0.1", port=2028)
    await worker.start_server()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        with tempfile.TemporaryDirectory(prefix="bug-browser-") as directory:
            fixture = asynccontextmanager(server_fixture.__wrapped__)
            async with fixture(PrivateAPI(settings), Path(directory)) as (_, http):

                async def control(request: web.Request) -> web.Response:
                    nonlocal services, coordinator, worker
                    if request.path == "/health":
                        return web.json_response({"ready": True})
                    if (
                        request.headers.get("Authorization")
                        != f"Bearer {CONTROL_TOKEN}"
                    ):
                        raise web.HTTPUnauthorized()
                    if request.method == "POST" and request.path == "/reset":
                        reset = ResetRequest.model_validate(await request.json())
                        await worker.close()
                        services = FakeServices(**reset.model_dump())
                        coordinator = CaseCoordinator(services.dependencies())
                        worker = TestServer(
                            create_app(settings, coordinator),
                            host="127.0.0.1",
                            port=2028,
                        )
                        await worker.start_server()
                    elif request.method == "POST" and request.path == "/stop-worker":
                        await worker.close()
                    elif request.method != "GET" or request.path != "/status":
                        raise web.HTTPNotFound()
                    return web.json_response(
                        {
                            "api_url": str(http.base_url).rstrip("/"),
                            "cases": len(coordinator.records),
                            "active": coordinator.active is not None,
                            "trace": services.trace,
                        }
                    )

                app = web.Application()
                app.router.add_route("*", "/{path:.*}", control)
                async with TestServer(app, host="127.0.0.1", port=2027):
                    await stop.wait()
    finally:
        await worker.close()


if __name__ == "__main__":
    asyncio.run(serve())
