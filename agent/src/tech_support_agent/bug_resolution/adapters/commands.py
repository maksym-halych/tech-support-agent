"""Bounded host subprocesses for trusted Git and Docker operations only."""

import asyncio
import os
import signal
from pathlib import Path


class CommandError(RuntimeError):
    """A trusted subprocess failed without exposing its raw environment/output."""

    def __init__(self, message: str, *, diagnostic: bytes = b"") -> None:
        super().__init__(message)
        self.diagnostic = diagnostic[:32768]


def command_environment() -> dict[str, str]:
    """Return CLI configuration only, never model, GitHub, or Telegram tokens."""

    allowed = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
    return {
        **{key: os.environ[key] for key in allowed if key in os.environ},
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


async def run_command(
    *args: str,
    cwd: Path | None = None,
    timeout: float = 30,
    max_output: int = 1_000_000,
    input_data: bytes | None = None,
) -> bytes:
    """Execute trusted argv with bounded output and process-group cleanup.

    Args:
        args: Fixed executable/arguments chosen by application code, not tools.
        cwd: Optional trusted working directory.
        timeout: Maximum elapsed seconds.
        max_output: Combined stdout and stderr byte budget.
        input_data: Optional bounded stdin payload.

    Returns:
        Captured stdout on success.
    """

    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env=command_environment(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    used = 0

    async def drain(stream: asyncio.StreamReader) -> bytes:
        nonlocal used
        chunks = []
        while chunk := await stream.read(65536):
            used += len(chunk)
            if used > max_output:
                raise CommandError("Command output limit exceeded")
            chunks.append(chunk)
        return b"".join(chunks)

    async def send() -> None:
        assert process.stdin is not None
        if input_data:
            process.stdin.write(input_data)
            await process.stdin.drain()
        process.stdin.close()

    assert process.stdout is not None and process.stderr is not None
    tasks = [
        asyncio.create_task(drain(process.stdout)),
        asyncio.create_task(drain(process.stderr)),
        asyncio.create_task(send()),
    ]
    try:
        async with asyncio.timeout(timeout):
            output, stderr, _ = await asyncio.gather(*tasks)
            if await process.wait() != 0:
                raise CommandError("Trusted command failed", diagnostic=stderr)
            return output
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await process.wait()
