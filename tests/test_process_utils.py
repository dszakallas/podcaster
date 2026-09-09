"""Tests for process utility wrapper and termination functions."""

import asyncio
import signal
import sys

import pytest

from podcaster.utils.process import (
    ProcessResult,
    ProcessTimeoutError,
    run_process,
    terminate_process,
)


@pytest.mark.anyio
async def test_run_process_success():
    """Verify run_process executes command, capturing stdout and stderr."""
    res = await run_process(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('hello'); sys.stderr.write('err')",
        ],
    )
    assert res.returncode == 0
    assert res.stdout == b"hello"
    assert res.stdout_text() == "hello"
    assert res.stderr == "err"


@pytest.mark.anyio
async def test_run_process_streaming_stderr():
    """Verify on_stderr_line callback is called for each stderr line."""
    lines = []

    def on_line(line: str) -> None:
        lines.append(line)

    res = await run_process(
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('line1\\nline2\\n'); sys.stderr.flush()",
        ],
        on_stderr_line=on_line,
    )
    assert res.returncode == 0
    assert lines == ["line1", "line2"]
    assert res.stderr == "line1\nline2"


@pytest.mark.anyio
async def test_run_process_timeout_terminates_with_sigterm():
    """Verify timeout sends SIGTERM and raises ProcessTimeoutError."""
    script = "import time, sys; sys.stdout.write('STARTED\\n'); sys.stdout.flush(); time.sleep(30)"
    with pytest.raises(
        ProcessTimeoutError, match="timed out after 0.2 seconds"
    ) as exc_info:
        await run_process(
            [sys.executable, "-c", script],
            timeout=0.2,
            grace_period=0.5,
            process_name="TestProc",
        )
    assert exc_info.value.timeout == 0.2


@pytest.mark.anyio
async def test_run_process_timeout_two_way_fallback_to_sigkill():
    """Verify process ignoring SIGTERM is forcefully killed with SIGKILL."""
    script = (
        "import signal, time, sys; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "sys.stdout.write('READY\\n'); "
        "sys.stdout.flush(); "
        "time.sleep(30)"
    )
    with pytest.raises(ProcessTimeoutError, match="timed out after 0.2 seconds"):
        await run_process(
            [sys.executable, "-c", script],
            timeout=0.2,
            grace_period=0.2,
        )


@pytest.mark.anyio
async def test_run_process_rejects_non_positive_timeout():
    """Verify non-positive timeout values raise ValueError."""
    with pytest.raises(ValueError, match="Timeout must be positive"):
        await run_process([sys.executable, "-c", "pass"], timeout=0)

    with pytest.raises(ValueError, match="Timeout must be positive"):
        await run_process([sys.executable, "-c", "pass"], timeout=-1)


@pytest.mark.anyio
async def test_terminate_process_already_exited():
    """Verify terminate_process is a no-op if process has already completed."""
    proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
    await proc.wait()
    assert proc.returncode == 0
    await terminate_process(proc)


@pytest.mark.anyio
async def test_terminate_process_kills_ignoring_sigterm():
    """Verify terminate_process falls back to SIGKILL if SIGTERM is ignored."""
    script = (
        "import signal, time, sys; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "sys.stdout.write('READY\\n'); "
        "sys.stdout.flush(); "
        "time.sleep(30)"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script, stdout=asyncio.subprocess.PIPE
    )
    assert proc.stdout is not None
    line = await proc.stdout.readline()
    assert line == b"READY\n"

    await terminate_process(proc, grace_period=0.1)
    assert proc.returncode is not None
    assert proc.returncode in (-signal.SIGKILL, -9)
