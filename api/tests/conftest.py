"""An isolated API with in-memory storage and a fixed clock."""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from lessons_api.app import create_app

ALICE = {"Authorization": "Bearer demo-alice"}
BOB = {"Authorization": "Bearer demo-bob"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(clock=lambda: datetime(2026, 10, 31, 9, tzinfo=UTC))
    with TestClient(app) as test_client:
        yield test_client
    app.state.store.close()
