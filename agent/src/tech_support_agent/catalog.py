"""Load and validate a local OpenAPI catalog without fetching remote references."""

import hashlib
import json
import re
from typing import Any

import yaml
from openapi_schema_validator import OAS30Validator, OAS31Validator
from openapi_spec_validator import validate

from tech_support_agent.models import (
    ApiConfig,
    ApiRequest,
    OperationDetails,
    OperationSummary,
)

HTTP_METHODS = {"get", "head", "post", "put", "patch", "delete", "options", "trace"}
RESERVED_HEADERS = {
    "authorization",
    "proxy-authorization",
    "host",
    "cookie",
    "set-cookie",
    "content-type",
    "content-length",
    "transfer-encoding",
    "connection",
    "accept",
}


class CatalogError(ValueError):
    """An invalid or unsupported API contract or operation argument."""


def _check_references(value: Any) -> None:
    if isinstance(value, dict):
        if "$ref" in value and not str(value["$ref"]).startswith("#/"):
            raise CatalogError("External OpenAPI references are not supported")
        for child in value.values():
            _check_references(child)
    elif isinstance(value, list):
        for child in value:
            _check_references(child)


def _resolve(
    value: Any, document: dict[str, Any], seen: frozenset[str] = frozenset()
) -> Any:
    if isinstance(value, list):
        return [_resolve(item, document, seen) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        ref = value["$ref"]
        if ref in seen:
            raise CatalogError("Recursive references are not supported in v1")
        target: Any = document
        try:
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError) as exc:
            raise CatalogError(f"Unresolved internal reference: {ref}") from exc
        resolved = _resolve(target, document, seen | {ref})
        siblings = {key: item for key, item in value.items() if key != "$ref"}
        return {**resolved, **_resolve(siblings, document, seen)}
    return {key: _resolve(item, document, seen) for key, item in value.items()}


class OpenApiCatalog:
    """Validated operation catalog, independent of any business domain."""

    def __init__(self, config: ApiConfig) -> None:
        """Read and validate the configured local JSON or YAML specification."""

        self.config = config
        if config.openapi_path.stat().st_size > 5 * 1024 * 1024:
            raise CatalogError("OpenAPI document exceeds 5 MiB")
        try:
            document = yaml.safe_load(config.openapi_path.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or not re.match(
                r"^3\.[01]\.\d+$", str(document.get("openapi", ""))
            ):
                raise CatalogError("Expected an OpenAPI 3.0 or 3.1 document")
            _check_references(document)
            validate(document)
        except CatalogError:
            raise
        except Exception as exc:
            raise CatalogError("Invalid OpenAPI document") from exc
        self.document = document
        self.validator = (
            OAS30Validator if document["openapi"].startswith("3.0.") else OAS31Validator
        )
        self.security_schemes = _resolve(
            document.get("components", {}).get("securitySchemes", {}), document
        )
        self.reserved_headers = RESERVED_HEADERS | {
            scheme["name"].lower()
            for scheme in self.security_schemes.values()
            if scheme.get("type") == "apiKey" and scheme.get("in") == "header"
        }
        if config.idempotency_header:
            self.reserved_headers.add(config.idempotency_header.lower())
        self.fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "spec": document,
                    "base_url": config.base_url,
                    "idempotency_header": config.idempotency_header,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        self.operations: dict[str, OperationDetails] = {}
        for path, raw_path_item in document["paths"].items():
            path_item = _resolve(raw_path_item, document)
            for method, operation in path_item.items():
                if method not in HTTP_METHODS:
                    continue
                parameters = {
                    (p["in"], p["name"]): p for p in path_item.get("parameters", [])
                }
                parameters.update(
                    {(p["in"], p["name"]): p for p in operation.get("parameters", [])}
                )
                operation_id = operation.get("operationId", f"{method.upper()} {path}")
                reason = self._unsupported(
                    path, method, operation, list(parameters.values())
                )
                details = OperationDetails(
                    operation_id=operation_id,
                    method=method.upper(),
                    path=path,
                    summary=operation.get("summary", operation_id),
                    description=operation.get("description", ""),
                    parameters=list(parameters.values()),
                    request_body=operation.get("requestBody"),
                    responses=operation["responses"],
                    security=operation.get("security", document.get("security", [])),
                    supported=reason is None,
                    unsupported_reason=reason,
                )
                if operation_id in self.operations:
                    raise CatalogError(f"Duplicate operation ID: {operation_id}")
                self.operations[operation_id] = details

    def _unsupported(
        self,
        path: str,
        method: str,
        operation: dict[str, Any],
        parameters: list[dict[str, Any]],
    ) -> str | None:
        if (
            not path.startswith("/")
            or path.startswith("//")
            or any(c in path for c in "\\?#")
            or ".." in path.split("/")
        ):
            return "Unsupported path template"
        if method == "trace":
            return "TRACE is not supported"
        for parameter in parameters:
            location = parameter["in"]
            if location not in {"path", "query", "header"} or "schema" not in parameter:
                return "Only schema-based path/query/header parameters are supported"
            default_style = "form" if location == "query" else "simple"
            if parameter.get("style", default_style) != default_style or parameter.get(
                "allowReserved"
            ):
                return "Unsupported parameter serialization"
        body = operation.get("requestBody")
        if body and "application/json" not in body.get("content", {}):
            return "Only application/json request bodies are supported"
        requirements = operation.get("security", self.document.get("security", []))
        for requirement in requirements:
            if all(self._supported_security(name) for name in requirement):
                return None
        if requirements:
            return "Only Bearer and header API-key authentication are supported"
        return None

    def _supported_security(self, name: str) -> bool:
        scheme = self.security_schemes.get(name, {})
        return (
            scheme.get("type") == "http"
            and scheme.get("scheme", "").lower() == "bearer"
        ) or (scheme.get("type") == "apiKey" and scheme.get("in") == "header")

    def list_operations(self) -> list[OperationSummary]:
        """Return compact entries without expanding every operation schema."""

        return [
            OperationSummary.model_validate(
                operation.model_dump(include=set(OperationSummary.model_fields))
            )
            for operation in self.operations.values()
        ]

    def get_operation(self, operation_id: str) -> OperationDetails:
        """Look up an operation by its documented or generated identifier."""

        if operation_id not in self.operations:
            raise CatalogError(f"Unknown operation: {operation_id}")
        return self.operations[operation_id].model_copy(deep=True)

    def validate_schema(self, value: Any, schema: dict[str, Any]) -> None:
        """Validate JSON using the specification's OpenAPI schema dialect."""

        validator = self.validator(schema, format_checker=self.validator.FORMAT_CHECKER)
        error = next(validator.iter_errors(value), None)
        if error:
            location = "/".join(str(part) for part in error.path) or "$"
            raise CatalogError(
                f"Schema validation failed at {location}: {error.validator}"
            )

    def validate_request(self, request: ApiRequest) -> OperationDetails:
        """Check required fields, schemas, and protected headers before any HTTP."""

        operation = self.get_operation(request.operation_id)
        if not operation.supported:
            raise CatalogError(operation.unsupported_reason)
        values = {
            "path": request.path_parameters,
            "query": request.query_parameters,
            "header": request.header_parameters,
        }
        for name in request.header_parameters:
            if name.lower() in self.reserved_headers or name.lower().startswith(
                "proxy-"
            ):
                raise CatalogError(f"Header is managed by the runtime: {name}")
        for location, provided in values.items():
            declared = {p["name"] for p in operation.parameters if p["in"] == location}
            if set(provided) - declared:
                raise CatalogError(f"Undocumented {location} parameters")
        for parameter in operation.parameters:
            name, location = parameter["name"], parameter["in"]
            if location == "header" and name.lower() in self.reserved_headers:
                managed = {"authorization", "accept", "content-type"} | {
                    scheme["name"].lower()
                    for scheme in self.security_schemes.values()
                    if scheme.get("type") == "apiKey" and scheme.get("in") == "header"
                }
                if self.config.idempotency_header and operation.method not in {
                    "GET",
                    "HEAD",
                }:
                    managed.add(self.config.idempotency_header.lower())
                if parameter.get("required") and name.lower() not in managed:
                    raise CatalogError(
                        f"Cannot supply required protected header: {name}"
                    )
                continue
            if name not in values[location]:
                if parameter.get("required"):
                    raise CatalogError(f"Missing required {location} parameter: {name}")
                continue
            value = values[location][name]
            self.validate_schema(value, parameter["schema"])
            if isinstance(value, dict) or (
                isinstance(value, list)
                and any(isinstance(v, (list, dict)) for v in value)
            ):
                raise CatalogError(
                    "Object and nested parameter serialization is not supported"
                )
            if location == "path" and (
                isinstance(value, list)
                or str(value) in {".", ".."}
                or any(c in str(value) for c in "/\\")
            ):
                raise CatalogError("Unsupported path parameter value")
            if location == "header" and any(c in str(value) for c in "\r\n"):
                raise CatalogError("Invalid header value")
        body = operation.request_body
        if body is None and request.body is not None:
            raise CatalogError("This operation does not accept a request body")
        if body:
            if request.body is None and body.get("required"):
                raise CatalogError("Missing required JSON request body")
            if request.body is not None:
                self.validate_schema(
                    request.body, body["content"]["application/json"].get("schema", {})
                )
        return operation
