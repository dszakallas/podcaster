"""Generic polling abstraction for long-running async tasks."""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterable, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel

from ..models import TaskStatus
from .retry import is_transient_network_exception

logger = logging.getLogger(__name__)


@dataclass
class PollStatus:
    status: TaskStatus
    error: str | None = None
    data: dict[str, Any] | None = None


class PollingJob[T]:
    """Generic polling coordinator for tasks with status checks, timeout, and intervals."""

    def __init__(
        self,
        check_status: Callable[[T], Awaitable[PollStatus]],
        timeout: float = 1800.0,
        interval: float | Callable[[float], float] = 10.0,
        is_transient: Callable[[Exception], bool] = is_transient_network_exception,
        on_timeout_fallback: Callable[[T], Awaitable[PollStatus | None]] | None = None,
        on_status_change: Callable[[T, PollStatus, float], None] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.check_status = check_status
        self.timeout = timeout
        self.interval = interval
        self.is_transient = is_transient
        self.on_timeout_fallback = on_timeout_fallback
        self.on_status_change = on_status_change
        self.sleep = sleep

    def get_interval(self, elapsed: float) -> float:
        if callable(self.interval):
            return self.interval(elapsed)
        return float(self.interval)

    async def poll_single(
        self,
        task: T,
        *,
        started_at: float | None = None,
    ) -> PollStatus:
        """Polls a single task until completion, failure, or timeout."""
        start_time = started_at if started_at is not None else time.time()
        last_status_label: str | None = None
        last_logged_at = 0.0

        while True:
            poll_status: PollStatus | None = None
            try:
                poll_status = await self.check_status(task)
                if poll_status.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                    return poll_status
            except Exception as exc:
                if not self.is_transient(exc):
                    logger.error("Non-retryable error polling task %s: %s", task, exc)
                    return PollStatus(status=TaskStatus.FAILED, error=str(exc))
                logger.warning("Transient error polling task %s: %s", task, exc)

            elapsed = time.time() - start_time

            now = time.time()
            current_label = (
                poll_status.data.get("raw_status")
                if (
                    poll_status
                    and poll_status.data
                    and "raw_status" in poll_status.data
                )
                else (poll_status.status.value if poll_status else "checking")
            )
            if self.on_status_change and (
                (now - last_logged_at >= 60.0) or (current_label != last_status_label)
            ):
                self.on_status_change(
                    task,
                    poll_status or PollStatus(status=TaskStatus.IN_PROGRESS),
                    elapsed,
                )
                last_logged_at = now
                last_status_label = current_label

            if elapsed > self.timeout:
                if self.on_timeout_fallback:
                    fallback = await self.on_timeout_fallback(task)
                    if fallback and fallback.status == TaskStatus.COMPLETED:
                        return fallback
                task_id = getattr(task, "task_id", str(task))
                return PollStatus(
                    status=TaskStatus.FAILED,
                    error=f"Task {task_id} timed out after {elapsed:.1f} seconds",
                )

            sleep_time = self.get_interval(elapsed)
            await self.sleep(sleep_time)

    async def poll_stream(
        self,
        tasks: AsyncIterable[T],
        *,
        concurrent: bool = False,
        update_task: Callable[[T, PollStatus], T] | None = None,
    ) -> AsyncGenerator[T, None]:
        """Polls an async stream of tasks and yields updated tasks upon completion."""

        def default_update(t: T, ps: PollStatus) -> T:
            if isinstance(t, BaseModel):
                update_dict: dict[str, Any] = {"status": ps.status}
                if ps.error is not None:
                    update_dict["error"] = ps.error
                if ps.data:
                    update_dict.update(ps.data)
                return cast(T, t.model_copy(update=update_dict))
            return t

        updater = update_task or default_update

        if not concurrent:
            async for task in tasks:
                poll_status = await self.poll_single(task)
                yield updater(task, poll_status)
        else:
            pending: set[asyncio.Task[tuple[T, PollStatus]]] = set()

            async def _run(t: T) -> tuple[T, PollStatus]:
                return t, await self.poll_single(t)

            async for task in tasks:
                pending.add(asyncio.create_task(_run(task)))

            try:
                while pending:
                    done, pending = await asyncio.wait(
                        pending, return_when=asyncio.FIRST_COMPLETED
                    )
                    for fut in done:
                        orig_task, poll_status = await fut
                        yield updater(orig_task, poll_status)
            finally:
                for fut in pending:
                    fut.cancel()
