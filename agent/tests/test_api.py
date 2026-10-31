import json

import httpx
import pytest

from tech_support_agent.api import ApiExecutor
from tech_support_agent.catalog import CatalogError, OpenApiCatalog
from tech_support_agent.models import (
    ApiConfig,
    ApiCredentials,
    ApiRequest,
    SupportContext,
)


async def test_reads_slots_in_user_timezone(
    api_config: ApiConfig, context: SupportContext, transport: httpx.ASGITransport
) -> None:
    response = await ApiExecutor(OpenApiCatalog(api_config), transport).execute(
        ApiRequest(
            operation_id="list_available_slots", query_parameters={"date": "2026-11-03"}
        ),
        context,
    )
    assert response.outcome == "success"
    assert len(response.data) == 6
    assert response.data[1]["starts_at"] == "2026-11-03T14:00:00+02:00"


async def test_no_slots_for_weekend(
    api_config: ApiConfig, context: SupportContext, transport: httpx.ASGITransport
) -> None:
    response = await ApiExecutor(OpenApiCatalog(api_config), transport).execute(
        ApiRequest(
            operation_id="list_available_slots", query_parameters={"date": "2026-11-01"}
        ),
        context,
    )
    assert response.data == []


async def test_missing_auth_prevents_http(api_config: ApiConfig) -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("HTTP must not execute without credentials")

    with pytest.raises(CatalogError, match="authorization"):
        await ApiExecutor(
            OpenApiCatalog(api_config), httpx.MockTransport(forbidden)
        ).execute(
            ApiRequest(operation_id="list_teachers"), SupportContext(user_id="alice")
        )


async def test_write_requires_action_id(
    api_config: ApiConfig, context: SupportContext
) -> None:
    with pytest.raises(CatalogError, match="approved"):
        await ApiExecutor(OpenApiCatalog(api_config)).execute(
            ApiRequest(
                operation_id="create_booking", body={"slot_id": "iryna:2026-11-03:14"}
            ),
            context,
        )


@pytest.mark.parametrize(
    "operation,expected,calls",
    [("list_teachers", "failed", 3), ("create_booking", "unknown_outcome", 1)],
)
async def test_network_failure_retries_only_reads(
    api_config: ApiConfig,
    context: SupportContext,
    operation: str,
    expected: str,
    calls: int,
) -> None:
    seen = []

    def timeout(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        raise httpx.ReadTimeout("secret internal error", request=request)

    request = ApiRequest(
        operation_id=operation,
        body={"slot_id": "iryna:2026-11-03:14"}
        if operation == "create_booking"
        else None,
    )
    response = await ApiExecutor(
        OpenApiCatalog(api_config), httpx.MockTransport(timeout)
    ).execute(request, context, "action-1")
    assert response.outcome == expected
    assert len(seen) == calls
    assert "secret internal error" not in response.model_dump_json()


@pytest.mark.parametrize("status", [401, 403, 409, 422])
async def test_known_http_failure(
    api_config: ApiConfig, context: SupportContext, status: int
) -> None:
    response = await ApiExecutor(
        OpenApiCatalog(api_config),
        httpx.MockTransport(lambda request: httpx.Response(status)),
    ).execute(
        ApiRequest(
            operation_id="create_booking", body={"slot_id": "iryna:2026-11-03:14"}
        ),
        context,
        "action-1",
    )
    assert response.outcome == "failed"
    assert response.status_code == status


async def test_malformed_success_after_write_is_unknown(
    api_config: ApiConfig, context: SupportContext
) -> None:
    response = await ApiExecutor(
        OpenApiCatalog(api_config),
        httpx.MockTransport(
            lambda request: httpx.Response(201, json={"unexpected": True})
        ),
    ).execute(
        ApiRequest(
            operation_id="create_booking", body={"slot_id": "iryna:2026-11-03:14"}
        ),
        context,
        "action-1",
    )
    assert response.outcome == "unknown_outcome"


@pytest.mark.parametrize("secret", ["private-key", 'private"\\key'])
async def test_credentials_and_api_key_reflections_are_redacted(
    api_config: ApiConfig,
    secret: str,
) -> None:
    document = json.loads(api_config.openapi_path.read_text())
    document["components"]["securitySchemes"] = {
        "KeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    }
    document["paths"] = {
        "/echo": {
            "get": {
                "operationId": "echo",
                "security": [{"KeyAuth": []}],
                "responses": {
                    "200": {
                        "description": "Echo",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                },
            }
        }
    }
    api_config.openapi_path.write_text(json.dumps(document))
    context = SupportContext(
        user_id="alice", credentials=ApiCredentials(api_keys={"KeyAuth": secret})
    )

    def echo(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == secret
        return httpx.Response(200, json={"echo": secret})

    response = await ApiExecutor(
        OpenApiCatalog(api_config), httpx.MockTransport(echo)
    ).execute(ApiRequest(operation_id="echo"), context)
    assert response.data == {"echo": "[REDACTED]"}
    assert "private-key" not in context.model_dump_json()


async def test_demo_idempotency_conflict_and_user_isolation(
    transport: httpx.ASGITransport,
) -> None:
    async with httpx.AsyncClient(
        transport=transport, base_url="http://demo.test"
    ) as client:
        headers = {"Authorization": "Bearer demo-alice", "Idempotency-Key": "once"}
        body = {"slot_id": "iryna:2026-11-03:14"}
        first = await client.post("/me/bookings", headers=headers, json=body)
        repeat = await client.post("/me/bookings", headers=headers, json=body)
        assert first.status_code == repeat.status_code == 201
        assert first.json()["id"] == repeat.json()["id"]
        conflict = await client.post(
            "/me/bookings", headers={**headers, "Idempotency-Key": "twice"}, json=body
        )
        assert conflict.status_code == 409
        different = await client.post(
            "/me/bookings", headers=headers, json={"slot_id": "oleh:2026-11-03:14"}
        )
        assert different.status_code == 409
        bob = {"Authorization": "Bearer demo-bob"}
        assert (await client.get("/me/bookings", headers=bob)).json() == []
        assert (
            await client.get(f"/me/bookings/{first.json()['id']}", headers=bob)
        ).status_code == 404
