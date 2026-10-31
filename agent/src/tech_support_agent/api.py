"""Bounded, authenticated HTTP execution for validated catalog operations."""

import asyncio
import json
from urllib.parse import quote

import httpx
from pydantic import JsonValue

from tech_support_agent.catalog import CatalogError, OpenApiCatalog
from tech_support_agent.models import (
    ApiRequest,
    ApiResponse,
    OperationDetails,
    SupportContext,
)


def redact(value: str, context: SupportContext) -> str:
    """Remove credentials even when a remote server reflects them in its response."""

    secrets = list(context.credentials.api_keys.values())
    if context.credentials.bearer_token:
        secrets.append(context.credentials.bearer_token)
    for secret in secrets:
        if token := secret.get_secret_value():
            value = value.replace(token, "[REDACTED]")
    return value


def _scalar(value: JsonValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def redact_json(value: JsonValue, context: SupportContext) -> JsonValue:
    """Remove secrets from decoded JSON, including strings with escape characters."""

    if isinstance(value, str):
        return redact(value, context)
    if isinstance(value, list):
        return [redact_json(item, context) for item in value]
    if isinstance(value, dict):
        return {
            redact(key, context): redact_json(item, context)
            for key, item in value.items()
        }
    return value


class ApiExecutor:
    """Execute only catalog requests; transport injection supports offline tests."""

    def __init__(
        self, catalog: OpenApiCatalog, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """Store the catalog and optional test transport without any credentials."""

        self.catalog = catalog
        self.transport = transport

    def auth_headers(
        self, operation: OperationDetails, context: SupportContext
    ) -> dict[str, str]:
        """Resolve a supported OpenAPI security alternative from trusted context."""

        if not operation.security:
            return {}
        for requirement in operation.security:
            headers: dict[str, str] = {}
            for name in requirement:
                scheme = self.catalog.security_schemes[name]
                if (
                    scheme.get("type") == "http"
                    and scheme.get("scheme", "").lower() == "bearer"
                    and context.credentials.bearer_token
                ):
                    headers["Authorization"] = (
                        f"Bearer {context.credentials.bearer_token.get_secret_value()}"
                    )
                elif (
                    scheme.get("type") == "apiKey"
                    and scheme.get("in") == "header"
                    and name in context.credentials.api_keys
                ):
                    headers[scheme["name"]] = context.credentials.api_keys[
                        name
                    ].get_secret_value()
                else:
                    break
            else:
                return headers
        raise CatalogError("API authorization is missing for this operation")

    async def execute(
        self, request: ApiRequest, context: SupportContext, action_id: str | None = None
    ) -> ApiResponse:
        """Validate and send one request, retrying only read operations."""

        operation = self.catalog.validate_request(request)
        is_read = operation.method in {"GET", "HEAD"}
        if not is_read and not action_id:
            raise CatalogError("A write requires an approved action ID")
        headers = {
            "Accept": "application/json",
            **self.auth_headers(operation, context),
        }
        headers.update(
            {
                name: ",".join(map(_scalar, value))
                if isinstance(value, list)
                else _scalar(value)
                for name, value in request.header_parameters.items()
            }
        )
        if not is_read and self.catalog.config.idempotency_header:
            headers[self.catalog.config.idempotency_header] = action_id
        path = operation.path
        for name, value in request.path_parameters.items():
            path = path.replace("{" + name + "}", quote(_scalar(value), safe=""))
        query: list[tuple[str, str]] = []
        for name, value in request.query_parameters.items():
            parameter = next(
                p
                for p in operation.parameters
                if p["in"] == "query" and p["name"] == name
            )
            if isinstance(value, list):
                if parameter.get("explode", True):
                    query.extend((name, _scalar(item)) for item in value)
                else:
                    query.append((name, ",".join(map(_scalar, value))))
            else:
                query.append((name, _scalar(value)))
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=20,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for attempt in range(3 if is_read else 1):
                try:
                    async with client.stream(
                        operation.method,
                        self.catalog.config.base_url + path,
                        params=query,
                        headers=headers,
                        json=request.body,
                    ) as response:
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > 1024 * 1024:
                                return ApiResponse(
                                    outcome="failed" if is_read else "unknown_outcome",
                                    status_code=response.status_code,
                                    error="API response exceeds 1 MiB; do not repeat a write",
                                )
                            chunks.append(chunk)
                        content = b"".join(chunks)
                        if (
                            is_read
                            and response.status_code in {429, 500, 502, 503, 504}
                            and attempt < 2
                        ):
                            await asyncio.sleep(0.1 * (2**attempt))
                            continue
                        return self._response(operation, response, content, context)
                except httpx.RequestError:
                    if is_read and attempt < 2:
                        await asyncio.sleep(0.1 * (2**attempt))
                        continue
                    return ApiResponse(
                        outcome="failed" if is_read else "unknown_outcome",
                        error="API request failed; result is unknown and must be reconciled before another write"
                        if not is_read
                        else "API is unavailable after three read attempts",
                    )
        raise RuntimeError("Unreachable HTTP retry state")

    def _response(
        self,
        operation: OperationDetails,
        response: httpx.Response,
        content: bytes,
        context: SupportContext,
    ) -> ApiResponse:
        is_read = operation.method in {"GET", "HEAD"}
        status = response.status_code
        if not 200 <= status < 300:
            outcome = (
                "unknown_outcome"
                if not is_read and (status >= 500 or 300 <= status < 400)
                else "failed"
            )
            return ApiResponse(
                outcome=outcome, status_code=status, error=f"API returned HTTP {status}"
            )
        try:
            if operation.method == "HEAD" or status == 204:
                data = None
            elif content:
                if (
                    response.headers.get("content-type", "").split(";")[0]
                    != "application/json"
                ):
                    raise CatalogError("Expected an application/json response")
                data = json.loads(content)
            else:
                data = None
            definition = operation.responses.get(
                str(status),
                operation.responses.get(
                    f"{status // 100}XX", operation.responses.get("default")
                ),
            )
            if definition is None:
                raise CatalogError("Undocumented success response")
            media = definition.get("content", {})
            if media and operation.method != "HEAD":
                if "application/json" not in media:
                    raise CatalogError("Only application/json responses are supported")
                self.catalog.validate_schema(
                    data, media["application/json"].get("schema", {})
                )
            sanitized = redact_json(data, context)
            return ApiResponse(outcome="success", status_code=status, data=sanitized)
        except ValueError, UnicodeDecodeError:
            return ApiResponse(
                outcome="failed" if is_read else "unknown_outcome",
                status_code=status,
                error="API returned an invalid or undocumented response; do not assume a write failed",
            )
