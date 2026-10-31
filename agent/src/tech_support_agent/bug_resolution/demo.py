"""Operator-only staging of demo bases; never a model-accessible capability."""

import argparse
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tech_support_agent.config import DemoSettings

ASSETS = Path(__file__).parent
TEMPLATE = ASSETS / "demo_template"
type Scenario = Literal["continue", "both"]


class StagingReceipt(BaseModel):
    """Explicit local staging outcome, not a claim about GitHub provisioning."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    repo: str
    directory: Path
    scenario: Scenario
    files: int


def demo_files(scenario: Scenario) -> dict[str, str]:
    """Load a complete scenario tree with exactly one ordinary source-code bug."""

    if scenario not in ("continue", "both"):
        raise ValueError("Unknown demo scenario")
    files = {
        path.relative_to(TEMPLATE).as_posix(): path.read_text()
        for path in TEMPLATE.rglob("*")
        if path.is_file()
    }
    for path in (ASSETS / "demo_variants" / scenario).glob("*.ts"):
        files[f"src/{path.name}"] = path.read_text()
    return files


def stage_demo(settings: DemoSettings, scenario: Scenario) -> StagingReceipt:
    """Stage files only into an operator-selected empty external directory.

    Does not initialize Git, create commits, contact GitHub, or run demo code.
    Use separate operator-created worktrees when staging the two base branches.
    """

    target = settings.demo_repo_dir
    support_root = ASSETS.parents[3].resolve()
    if target.resolve().is_relative_to(support_root):
        raise ValueError("Demo must be outside the support repository")
    if any(part.is_symlink() for part in (target, *target.parents)):
        raise ValueError("Demo directory cannot traverse symlinks")
    if not target.parent.is_dir():
        raise ValueError("Operator must create the parent directory first")
    if target.exists() and any(p.name != ".git" for p in target.iterdir()):
        raise ValueError("Refusing to overwrite a nonempty demo directory")
    files = demo_files(scenario)
    target.mkdir(exist_ok=True)
    for relative, content in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as output:
            output.write(content)
    return StagingReceipt(
        repo=settings.demo_repo, directory=target, scenario=scenario, files=len(files)
    )


def main() -> None:
    """Validate configuration or explicitly stage one base without Git side effects."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("continue", "both"), required=True)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Stage files; no commits or remote operations",
    )
    args = parser.parse_args()
    try:
        settings = DemoSettings()
    except ValueError:
        parser.exit(
            2,
            "Configure BUG_DEMO_REPO=owner/name and BUG_DEMO_REPO_DIR=/absolute/external/path. Values suppressed.\n",
        )
    if not args.write:
        print(
            json.dumps(
                {"configured": True, "scenario": args.scenario, "written": False}
            )
        )
        return
    try:
        receipt = stage_demo(settings, args.scenario)
    except OSError, ValueError:
        parser.exit(
            2,
            "Cannot stage demo: require an empty external non-symlink directory with an existing parent.\n",
        )
    print(receipt.model_dump_json())


if __name__ == "__main__":
    main()
