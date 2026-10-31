"""Validated commands and results shared by the adapter and its tools."""

import re
from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_FILE = 65_536
MAX_LINE = 3_000_000
MAX_PNG = 2_000_000
HARNESS_PATHS = frozenset(
    {
        "index.html",
        "package.json",
        "pnpm-lock.yaml",
        "tsconfig.json",
        "vite.config.ts",
        "playwright.config.ts",
        "tests/smoke.spec.ts",
        ".github/workflows/verify.yml",
        ".gitignore",
    }
)


def safe_path(value: str) -> str:
    """Accept only canonical, visible application source or smoke-test paths."""

    parts = value.split("/")
    if (
        not value
        or len(value) > 200
        or not re.fullmatch(r"[A-Za-z0-9_./-]+", value)
        or any(
            p in ("", ".", "..", "node_modules", "dist") or p.startswith(".")
            for p in parts
        )
        or PurePosixPath(value).is_absolute()
        or not (
            value.startswith("src/")
            and PurePosixPath(value).suffix in (".ts", ".tsx", ".css")
            or value in ("tests/smoke.spec.ts", "tests/regression.spec.ts")
        )
    ):
        raise ValueError("Path is outside the read-only source allowlist")
    return value


def relative_url(value: str) -> str:
    """Reject foreign, encoded, ambiguous, and non-relative navigation."""

    decoded = unquote(value)
    parsed = urlsplit(decoded)
    if (
        len(value) > 500
        or not value.startswith("/")
        or value.startswith("//")
        or decoded != value
        or "\\" in value
        or any(ord(c) <= 32 or ord(c) >= 127 for c in value)
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or any(p in (".", "..") for p in parsed.path.split("/"))
    ):
        raise ValueError("Only plain demo-relative paths are allowed")
    return value


class WireModel(BaseModel):
    """Strict protocol boundary; unknown command fields are never ignored."""

    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )


class Empty(WireModel):
    op: Literal["inspect", "screenshot"]


class Navigate(WireModel):
    op: Literal["navigate"] = "navigate"
    path: str
    validate_path = field_validator("path")(relative_url)


class Click(WireModel):
    op: Literal["click"] = "click"
    ref: str = Field(pattern=r"^[a-f0-9]{16}:[0-9]{1,3}$")


class Select(WireModel):
    op: Literal["select"] = "select"
    ref: str = Field(pattern=r"^[a-f0-9]{16}:[0-9]{1,3}$")
    value: str = Field(min_length=1, max_length=100)


class ReadFile(WireModel):
    op: Literal["read_file"] = "read_file"
    path: str
    validate_path = field_validator("path")(safe_path)


class Search(WireModel):
    op: Literal["search_code"] = "search_code"
    query: str = Field(min_length=1, max_length=100)


class RunCheck(WireModel):
    op: Literal["check"] = "check"
    kind: Literal["typecheck", "tests", "build"]


class SourceFile(WireModel):
    path: str
    content: str = Field(max_length=MAX_FILE)

    @field_validator("path")
    @classmethod
    def source_or_harness(cls, value: str) -> str:
        """Permit trusted harness files only at bootstrap, never model tools."""

        return value if value in HARNESS_PATHS else safe_path(value)


class Prepare(WireModel):
    op: Literal["prepare"] = "prepare"
    files: list[SourceFile] = Field(min_length=1, max_length=100)


type Operation = Annotated[
    Empty | Navigate | Click | Select | ReadFile | Search | RunCheck | Prepare,
    Field(discriminator="op"),
]


class Request(WireModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    command: Operation


class Element(WireModel):
    ref: str
    role: str = Field(max_length=40)
    name: str = Field(max_length=200)
    disabled: bool
    value: str = Field(max_length=100)


class PageSnapshot(WireModel):
    kind: Literal["page"] = "page"
    path: str
    text: str = Field(max_length=20_000)
    elements: list[Element] = Field(max_length=100)
    validate_path = field_validator("path")(relative_url)


class FileContent(WireModel):
    kind: Literal["file"] = "file"
    path: str
    content: str = Field(max_length=MAX_FILE)
    validate_path = field_validator("path")(safe_path)


class SearchHit(WireModel):
    path: str
    line: int = Field(ge=1)
    text: str = Field(max_length=300)
    validate_path = field_validator("path")(safe_path)


class SearchResult(WireModel):
    kind: Literal["search"] = "search"
    hits: list[SearchHit] = Field(max_length=100)


class Screenshot(WireModel):
    kind: Literal["png"] = "png"
    artifact_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    mime: Literal["image/png"]
    data: str = Field(max_length=2_666_668, repr=False)


class CheckResult(WireModel):
    kind: Literal["check"] = "check"
    check: Literal["typecheck", "tests", "build"]
    status: Literal["passed", "failed", "timeout"]
    exit_code: int | None
    output: str = Field(max_length=32_768)

    @model_validator(mode="after")
    def consistent_exit(self) -> CheckResult:
        """Do not accept a contradictory passed/failed exit status."""

        if self.status == "passed" and self.exit_code != 0:
            raise ValueError("Passed check must exit zero")
        if self.status != "passed" and self.exit_code == 0:
            raise ValueError("Failed or timed-out check cannot exit zero")
        return self


class Ready(WireModel):
    kind: Literal["ready"] = "ready"


type Result = Annotated[
    PageSnapshot | FileContent | SearchResult | Screenshot | CheckResult | Ready,
    Field(discriminator="kind"),
]


class Reply(WireModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    result: Result | None = None
    error: (
        Literal["invalid_request", "policy", "stale_ref", "operation_failed"] | None
    ) = None

    @model_validator(mode="after")
    def one_outcome(self) -> Reply:
        """Require exactly one success result or failure code."""

        if (self.result is None) == (self.error is None):
            raise ValueError("Reply must contain exactly one outcome")
        return self
