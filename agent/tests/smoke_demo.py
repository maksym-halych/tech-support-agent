"""Exercise the real CLI and loopback API without a paid language-model request.

Run with ``uv run --directory agent python -m tests.smoke_demo``.
The scripted model isolates CLI/HTTP/persistence verification from LLM quality.
"""

import asyncio
import json
import socket
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
import uvicorn

from tech_support_agent.cli import run_chat
from tech_support_agent.config import Settings
from tests.conftest import call, finish, scripted_model
from tests.demo_api import create_demo_app


async def exercise(settings: Settings, base_url: str) -> None:
    """Run a choice, exit at approval, restore, and verify exactly one booking."""

    today = datetime.now(ZoneInfo("Europe/Kyiv")).date()
    target = today + timedelta(days=(1 - today.weekday()) % 7 or 7)
    model = scripted_model(
        call("get_current_datetime"),
        call("list_operations"),
        call("get_operation", operation_id="list_available_slots"),
        call(
            "call_operation",
            operation_id="list_available_slots",
            query_parameters={"date": target.isoformat()},
        ),
        finish("О 14:00 доступні Ірина й Олег. Кого обираєте?", "needs_input"),
        call("get_operation", operation_id="create_booking"),
        call(
            "call_operation",
            operation_id="create_booking",
            body={"slot_id": f"iryna:{target.isoformat()}:14"},
            confirmation_summary=f"Запис до Ірини Коваль {target.isoformat()} о 14:00 Europe/Kyiv",
        ),
    )
    with (
        patch("tech_support_agent.cli.create_chat_model", return_value=model),
        patch(
            "builtins.input",
            side_effect=["Запиши на наступний вівторок о 14:00", "До Ірини", "exit"],
        ),
    ):
        await run_chat(settings, "smoke-thread")
    async with httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": "Bearer demo-alice"},
        trust_env=False,
    ) as client:
        assert (await client.get("/me/bookings")).json() == [], (
            "A write happened before approval"
        )
        with (
            patch(
                "tech_support_agent.cli.create_chat_model",
                return_value=scripted_model(finish("Запис підтверджено")),
            ),
            patch("builtins.input", side_effect=["approve", "/exit"]),
        ):
            await run_chat(settings, "smoke-thread")
        records = (await client.get("/me/bookings")).json()
        assert len(records) == 1
        assert records[0]["slot"]["id"] == f"iryna:{target.isoformat()}:14"
        print(
            f"SMOKE OK: one confirmed booking {records[0]['id']}, restored from SQLite; real loopback HTTP, scripted model."
        )


def main() -> None:
    """Start and stop an isolated loopback service around the CLI smoke run."""

    data_root = Path("data").resolve()
    data_root.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="smoke-", dir=data_root) as temporary:
        root = Path(temporary)
        app = create_demo_app(root / "demo.sqlite")
        document = root / "openapi.json"
        document.write_text(json.dumps(app.openapi()), encoding="utf-8")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
            thread = Thread(
                target=server.run, kwargs={"sockets": [listener]}, daemon=True
            )
            thread.start()
            settings = Settings(
                _env_file=None,
                support_openapi_path=document,
                support_api_base_url=f"http://127.0.0.1:{port}",
                support_checkpoint_path=root / "graph.sqlite",
                support_journal_path=root / "journal.sqlite",
                support_user_id="alice",
                support_api_token="demo-alice",
                support_idempotency_header="Idempotency-Key",
            )
            try:
                asyncio.run(exercise(settings, settings.support_api_base_url))
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                if thread.is_alive():
                    raise RuntimeError("Demo server did not stop")
                app.state.connection.close()


if __name__ == "__main__":
    main()
