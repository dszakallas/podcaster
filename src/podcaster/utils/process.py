from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SHUTDOWN_GRACE_PERIOD: float = 5.0


class ProcessTimeoutError(TimeoutError):
    """Raised when a subprocess exceeds its execution timeout."""

    def __init__(
        self,
        message: str,
        *,
        timeout: float | None = None,
        stdout: bytes = b"",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.timeout = timeout
        self.stdout = stdout
        self.stderr = stderr


@dataclass
class ProcessResult:
    """Encapsulates the completion state of a subprocess."""

    returncode: int
    stdout: bytes
    stderr: str

    def stdout_text(self, encoding: str = "utf-8", errors: str = "replace") -> str:
        """Decodes stdout bytes to a string."""
        return self.stdout.decode(encoding, errors=errors)


async def terminate_process(
    process: asyncio.subprocess.Process,
    *,
    grace_period: float = DEFAULT_SHUTDOWN_GRACE_PERIOD,
) -> None:
    """Terminates a process using SIGTERM first, then SIGKILL if ungraceful."""
    if process.returncode is not None:
        return

    logger.warning("Sending SIGTERM to process (PID %s)...", process.pid)
    try:
        process.terminate()
    except ProcessLookupError:
        return

    try:
        await asyncio.wait_for(process.wait(), timeout=grace_period)
        return
    except TimeoutError:
        pass

    if process.returncode is not None:
        return

    logger.warning(
        "Process (PID %s) did not exit after SIGTERM; sending SIGKILL...",
        process.pid,
    )
    try:
        process.kill()
    except ProcessLookupError:
        return

    await process.wait()


async def run_process(
    cmd_args: Sequence[str],
    *,
    timeout: float | None = None,
    grace_period: float = DEFAULT_SHUTDOWN_GRACE_PERIOD,
    on_stderr_line: Callable[[str], None] | None = None,
    process_name: str | None = None,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> ProcessResult:
    """Runs a subprocess with streaming stderr, timeout, and two-way SIGTERM -> SIGKILL termination."""
    if timeout is not None and timeout <= 0:
        raise ValueError(f"Timeout must be positive, got {timeout}")

    name = process_name or (os.path.basename(cmd_args[0]) if cmd_args else "Process")

    process = await asyncio.create_subprocess_exec(
        *cmd_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )

    stdout_chunks: list[bytes] = []
    stderr_lines: list[str] = []
    timed_out = False

    async def _read_stdout() -> None:
        if process.stdout:
            stdout_chunks.append(await process.stdout.read())

    async def _read_stderr() -> None:
        if process.stderr:
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                line_str = line.decode().rstrip()
                if line_str:
                    stderr_lines.append(line_str)
                    if on_stderr_line:
                        on_stderr_line(line_str)

    async def _wait_process() -> None:
        nonlocal timed_out
        if timeout is not None and timeout > 0:
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except TimeoutError:
                timed_out = True
                logger.warning(
                    "%s (PID %s) timed out after %s seconds.",
                    name,
                    process.pid,
                    timeout,
                )
                await terminate_process(process, grace_period=grace_period)
        else:
            await process.wait()

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(_read_stdout())
            tg.create_task(_read_stderr())
            tg.create_task(_wait_process())
    finally:
        if process.returncode is None:
            await terminate_process(process, grace_period=grace_period)

    stdout_bytes = b"".join(stdout_chunks)
    stderr_text = "\n".join(stderr_lines).strip()
    if timed_out:
        detail = f": {stderr_text}" if stderr_text else ""
        raise ProcessTimeoutError(
            f"{name} timed out after {timeout} seconds{detail}",
            timeout=timeout,
            stdout=stdout_bytes,
            stderr=stderr_text,
        )

    return ProcessResult(
        returncode=process.returncode if process.returncode is not None else 0,
        stdout=stdout_bytes,
        stderr=stderr_text,
    )
