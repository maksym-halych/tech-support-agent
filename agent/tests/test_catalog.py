import json
from pathlib import Path

import pytest
import yaml

from tech_support_agent.catalog import CatalogError, OpenApiCatalog
from tech_support_agent.models import ApiConfig, ApiRequest


def test_resolves_internal_refs_and_lists_operations(api_config: ApiConfig) -> None:
    catalog = OpenApiCatalog(api_config)
    operation = catalog.get_operation("create_booking")
    assert operation.supported
    assert "$ref" not in json.dumps(operation.model_dump())
    assert (
        "slot_id"
        in operation.request_body["content"]["application/json"]["schema"]["properties"]
    )
    assert len(catalog.list_operations()) == 5


@pytest.mark.parametrize("version", ["3.0.3", "3.1.0"])
def test_yaml_and_both_openapi_versions(tmp_path: Path, version: str) -> None:
    spec = {
        "openapi": version,
        "info": {"title": "Inventory", "version": "1"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "inventory",
                    "responses": {
                        "200": {
                            "description": "items",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }
    path = tmp_path / "inventory.yaml"
    path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    catalog = OpenApiCatalog(
        ApiConfig(openapi_path=path, base_url="https://inventory.test")
    )
    assert (
        catalog.validate_request(ApiRequest(operation_id="inventory")).method == "GET"
    )


@pytest.mark.parametrize(
    "api_request",
    [
        ApiRequest(operation_id="missing"),
        ApiRequest(operation_id="list_available_slots"),
        ApiRequest(
            operation_id="list_available_slots", query_parameters={"date": "not-a-date"}
        ),
        ApiRequest(
            operation_id="list_available_slots",
            query_parameters={"date": "2026-11-03", "url": "https://other.test"},
        ),
        ApiRequest(operation_id="create_booking", body={}),
        ApiRequest(
            operation_id="create_booking", body={"slot_id": "slot", "user_id": "bob"}
        ),
        ApiRequest(
            operation_id="create_booking",
            body={"slot_id": "slot"},
            header_parameters={"Authorization": "Bearer evil"},
        ),
        ApiRequest(
            operation_id="create_booking",
            body={"slot_id": "slot"},
            header_parameters={"Idempotency-Key": "reuse"},
        ),
        ApiRequest(operation_id="get_my_booking", path_parameters={"booking_id": ".."}),
    ],
)
def test_invalid_requests_are_rejected(
    api_config: ApiConfig, api_request: ApiRequest
) -> None:
    with pytest.raises(CatalogError):
        OpenApiCatalog(api_config).validate_request(api_request)


def test_external_reference_is_rejected_before_validation_or_network(
    api_config: ApiConfig,
) -> None:
    document = json.loads(api_config.openapi_path.read_text())
    document["paths"]["/evil"] = {"$ref": "http://127.0.0.1/private"}
    api_config.openapi_path.write_text(json.dumps(document))
    with pytest.raises(CatalogError, match="External"):
        OpenApiCatalog(api_config)


def test_unsupported_body_is_visible_but_not_executable(api_config: ApiConfig) -> None:
    document = json.loads(api_config.openapi_path.read_text())
    document["paths"]["/me/bookings"]["post"]["requestBody"] = {
        "content": {"text/plain": {"schema": {"type": "string"}}}
    }
    api_config.openapi_path.write_text(json.dumps(document))
    catalog = OpenApiCatalog(api_config)
    assert not catalog.get_operation("create_booking").supported
    with pytest.raises(CatalogError, match="application/json"):
        catalog.validate_request(ApiRequest(operation_id="create_booking", body="text"))


def test_invalid_document(api_config: ApiConfig) -> None:
    api_config.openapi_path.write_text('{"openapi": "3.1.0"}')
    with pytest.raises(CatalogError, match="Invalid"):
        OpenApiCatalog(api_config)
