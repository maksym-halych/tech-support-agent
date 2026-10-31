"""Command-line entry points: serve the API and export its OpenAPI document."""

import argparse
import json
from pathlib import Path
from typing import NoReturn

import uvicorn

from lessons_api.app import create_app
from lessons_api.auth import TokenVerifier, jwks_key_resolver
from lessons_api.config import get_settings


def export_openapi() -> None:
    """Write the API's OpenAPI document to a JSON file."""

    parser = argparse.ArgumentParser(description="Export the lessons API contract")
    parser.add_argument("--output", type=Path, default=Path("openapi.json"))
    args = parser.parse_args()

    def no_keys(token: str) -> NoReturn:
        raise RuntimeError("Exporting the contract never verifies tokens")

    app = create_app(TokenVerifier("https://clerk.invalid", [], no_keys))
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    finally:
        app.state.store.close()


def main() -> None:
    """Serve the API with uvicorn using ``LESSONS_API_*`` settings."""

    settings = get_settings()
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    verifier = TokenVerifier(
        settings.clerk_issuer,
        settings.clerk_authorized_parties,
        jwks_key_resolver(settings.clerk_issuer),
    )
    app = create_app(
        verifier, settings.database_path, cors_origins=settings.cors_origins
    )
    try:
        uvicorn.run(app, host=settings.host, port=settings.port)
    finally:
        app.state.store.close()


if __name__ == "__main__":
    main()
