"""A bounded client with no general-purpose route or resume method."""

from typing import TypeVar
from urllib.parse import quote

from aiohttp import ClientError, ClientSession, ClientTimeout
from pydantic import BaseModel, TypeAdapter, ValidationError

from tech_support_agent.bug_resolution.contracts import (
    MAX_BODY_BYTES,
    MAX_PUBLIC_RESPONSE_BYTES,
    CaseReceipt,
    CaseRequest,
    ClarificationRequest,
    EventReceipt,
    PublicEvent,
    PublicEventQuery,
)
from tech_support_agent.config import BugSettings

T = TypeVar("T")


class WorkerUnavailable(Exception):
    """Safe transport failure with no URL, token, response body or raw exception."""


class PrivateAPI:
    """Short-lived sessions avoid event-loop and Agent Server factory lifetime leaks."""

    def __init__(self, settings: BugSettings) -> None:
        self.settings = settings

    async def _request(
        self,
        method: str,
        path: str,
        response_type: TypeAdapter[T],
        body: BaseModel | None = None,
        query: BaseModel | None = None,
    ) -> T:
        payload = body.model_dump_json().encode() if body else None
        if payload and len(payload) > MAX_BODY_BYTES:
            raise WorkerUnavailable()
        try:
            async with (
                ClientSession(
                    timeout=ClientTimeout(total=self.settings.request_timeout),
                    trust_env=False,
                ) as session,
                session.request(
                    method,
                    f"{self.settings.worker_url}{path}",
                    headers={
                        "Authorization": f"Bearer {self.settings.worker_token.get_secret_value()}",
                        "Content-Type": "application/json",
                    },
                    data=payload,
                    params=query.model_dump() if query else None,
                    allow_redirects=False,
                ) as response,
            ):
                if response.status != 200:
                    raise WorkerUnavailable()
                raw = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    if len(raw) + len(chunk) > MAX_PUBLIC_RESPONSE_BYTES:
                        raise WorkerUnavailable()
                    raw.extend(chunk)
                return response_type.validate_json(bytes(raw))
        except ClientError, TimeoutError, ValidationError, ValueError:
            raise WorkerUnavailable() from None

    async def accept_case(self, request: CaseRequest) -> CaseReceipt:
        """Submit one intake request or recover its existing thread binding."""

        return await self._request(
            "POST", "/cases", TypeAdapter(CaseReceipt), body=request
        )

    async def public_event(self, case_id: str, query: PublicEventQuery) -> PublicEvent:
        """Read one event, with explicit thread binding and delivery cursor."""

        return await self._request(
            "GET",
            f"/cases/{quote(case_id, safe='')}/public-event",
            TypeAdapter(PublicEvent),
            query=query,
        )

    async def clarify(
        self, case_id: str, request: ClarificationRequest
    ) -> EventReceipt:
        """Send a typed answer, not a graph command."""

        return await self._request(
            "POST",
            f"/cases/{quote(case_id, safe='')}/clarification",
            TypeAdapter(EventReceipt),
            body=request,
        )
