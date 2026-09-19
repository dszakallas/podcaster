"""Unit tests for PollingJob abstraction."""

from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from podcaster.models import TaskStatus
from podcaster.utils.polling import PollingJob, PollStatus


class DummyTask(BaseModel):
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    error: str | None = None


@pytest.mark.anyio
async def test_polling_job_immediate_success():
    async def check(task: DummyTask) -> PollStatus:
        return PollStatus(status=TaskStatus.COMPLETED)

    job = PollingJob(check_status=check)
    res = await job.poll_single(DummyTask(task_id="t1"))
    assert res.status == TaskStatus.COMPLETED


@pytest.mark.anyio
async def test_polling_job_multiple_attempts():
    attempts = 0

    async def check(task: DummyTask) -> PollStatus:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return PollStatus(status=TaskStatus.IN_PROGRESS)
        return PollStatus(status=TaskStatus.COMPLETED)

    mock_sleep = AsyncMock()
    job = PollingJob(check_status=check, interval=1.0, sleep=mock_sleep)
    res = await job.poll_single(DummyTask(task_id="t2"))

    assert res.status == TaskStatus.COMPLETED
    assert attempts == 3
    assert mock_sleep.await_count == 2


@pytest.mark.anyio
async def test_polling_job_non_retryable_error():
    async def check(task: DummyTask) -> PollStatus:
        raise ValueError("Fatal error occurred")

    job = PollingJob(check_status=check)
    res = await job.poll_single(DummyTask(task_id="t3"))

    assert res.status == TaskStatus.FAILED
    assert "Fatal error occurred" in (res.error or "")


@pytest.mark.anyio
async def test_polling_job_transient_error_retry():
    calls = 0

    async def check(task: DummyTask) -> PollStatus:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionResetError("Connection lost")
        return PollStatus(status=TaskStatus.COMPLETED)

    mock_sleep = AsyncMock()
    job = PollingJob(
        check_status=check,
        interval=1.0,
        is_transient=lambda e: isinstance(e, ConnectionResetError),
        sleep=mock_sleep,
    )
    res = await job.poll_single(DummyTask(task_id="t4"))

    assert res.status == TaskStatus.COMPLETED
    assert calls == 2
    assert mock_sleep.await_count == 1


@pytest.mark.anyio
async def test_polling_job_timeout():
    async def check(task: DummyTask) -> PollStatus:
        return PollStatus(status=TaskStatus.IN_PROGRESS)

    job = PollingJob(check_status=check, timeout=0.1, interval=0.05)
    res = await job.poll_single(DummyTask(task_id="t5"), started_at=0.0)

    assert res.status == TaskStatus.FAILED
    assert "timed out" in (res.error or "")


@pytest.mark.anyio
async def test_polling_job_timeout_fallback():
    async def check(task: DummyTask) -> PollStatus:
        return PollStatus(status=TaskStatus.IN_PROGRESS)

    async def fallback(task: DummyTask) -> PollStatus | None:
        return PollStatus(status=TaskStatus.COMPLETED)

    job = PollingJob(
        check_status=check,
        timeout=0.1,
        interval=0.05,
        on_timeout_fallback=fallback,
    )
    res = await job.poll_single(DummyTask(task_id="t6"), started_at=0.0)

    assert res.status == TaskStatus.COMPLETED


@pytest.mark.anyio
async def test_polling_job_stream_sequential():
    async def check(task: DummyTask) -> PollStatus:
        return PollStatus(status=TaskStatus.COMPLETED)

    tasks = [DummyTask(task_id="s1"), DummyTask(task_id="s2")]

    async def aiter_tasks():
        for t in tasks:
            yield t

    job = PollingJob(check_status=check)
    results = [res async for res in job.poll_stream(aiter_tasks())]

    assert len(results) == 2
    assert results[0].status == TaskStatus.COMPLETED
    assert results[1].status == TaskStatus.COMPLETED


@pytest.mark.anyio
async def test_polling_job_stream_concurrent():
    async def check(task: DummyTask) -> PollStatus:
        return PollStatus(status=TaskStatus.COMPLETED)

    tasks = [DummyTask(task_id="c1"), DummyTask(task_id="c2")]

    async def aiter_tasks():
        for t in tasks:
            yield t

    job = PollingJob(check_status=check)
    results = [res async for res in job.poll_stream(aiter_tasks(), concurrent=True)]

    assert len(results) == 2
    ids = {r.task_id for r in results}
    assert ids == {"c1", "c2"}
    assert all(r.status == TaskStatus.COMPLETED for r in results)


@pytest.mark.anyio
async def test_polling_job_status_change_callback():
    statuses = ["queued", "running", "running"]
    idx = 0

    async def check(task: DummyTask) -> PollStatus:
        nonlocal idx
        st = statuses[min(idx, len(statuses) - 1)]
        idx += 1
        if idx >= 3:
            return PollStatus(status=TaskStatus.COMPLETED, data={"raw_status": st})
        return PollStatus(status=TaskStatus.IN_PROGRESS, data={"raw_status": st})

    callbacks: list[str] = []

    def on_change(task: DummyTask, ps: PollStatus, elapsed: float):
        raw = ps.data.get("raw_status") if ps.data else ps.status.value
        if raw:
            callbacks.append(raw)

    mock_sleep = AsyncMock()
    job = PollingJob(
        check_status=check,
        interval=1.0,
        on_status_change=on_change,
        sleep=mock_sleep,
    )
    res = await job.poll_single(DummyTask(task_id="t7"))
    assert res.status == TaskStatus.COMPLETED
    assert callbacks == ["queued", "running"]
