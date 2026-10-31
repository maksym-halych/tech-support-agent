"""Operator-only image build and loopback preview; never starts the bug worker."""

import argparse
import asyncio
import signal

from tech_support_agent.bug_resolution.adapters.sandbox import (
    DockerSandbox,
    build_image,
    capture_profile,
)
from tech_support_agent.bug_resolution.demo import Scenario
from tech_support_agent.config import DemoSettings


async def preview(settings: DemoSettings, scenario: Scenario) -> None:
    """Keep one configured base running until the operator sends SIGINT/SIGTERM."""

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    sandbox = None
    try:
        profile = await capture_profile(settings, scenario)
        sandbox = await DockerSandbox.prepare(profile)
        print(
            f"Demo preview: {sandbox.url} | base {profile.base_sha} | Ctrl-C to clean up",
            flush=True,
        )
        await stop.wait()
    finally:
        if sandbox is not None:
            await sandbox.close()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


def main() -> None:
    """Build trusted assets or preview an explicitly configured external repository."""

    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--build-image", action="store_true")
    group.add_argument("--preview", choices=("continue", "both"))
    args = parser.parse_args()
    if args.build_image:
        asyncio.run(build_image())
        print("Built bug-resolution-sandbox:phase2 (trusted operator operation)")
        return
    try:
        settings = DemoSettings()
    except ValueError:
        parser.exit(
            2,
            "Configure BUG_DEMO_REPO and BUG_DEMO_REPO_DIR before preview. Values suppressed.\n",
        )
    asyncio.run(preview(settings, args.preview))


if __name__ == "__main__":
    main()
