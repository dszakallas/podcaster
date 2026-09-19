"""Transient-network detection and retry helpers."""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import httpx
from notebooklm.exceptions import NetworkError
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
RETRYABLE_GRPC_STATUSES = {
    "UNAVAILABLE",
    "DEADLINE_EXCEEDED",
    "RESOURCE_EXHAUSTED",
    "INTERNAL",
}
RETRYABLE_CLASS_NAMES = {
    "ServiceUnavailable",
    "DeadlineExceeded",
    "ResourceExhausted",
    "TooManyRequests",
    "InternalServerError",
    "BadGateway",
    "GatewayTimeout",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "ConnectError",
    "NetworkError",
}


def is_transient_network_exception(error: BaseException) -> bool:
    """Return whether an exception is safe to retry as a transient network failure."""
    if not isinstance(error, Exception):
        return False
    if isinstance(error, (ConnectionError, TimeoutError, OSError, NetworkError)):
        return True
    if isinstance(
        error,
        (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.ConnectError,
            httpx.ProtocolError,
        ),
    ):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in RETRYABLE_STATUS_CODES
    if error.__class__.__name__ in RETRYABLE_CLASS_NAMES:
        return True
    code = getattr(error, "code", None)
    if callable(code):
        try:
            code = code()
        except Exception:
            code = None
    if hasattr(code, "name"):
        return str(getattr(code, "name", "")) in RETRYABLE_GRPC_STATUSES
    return isinstance(code, int) and code in RETRYABLE_STATUS_CODES


async def retry_rpc(
    function: Callable[..., Any],
    *args: Any,
    retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    jitter: float | None = None,
    logger: logging.Logger | None = None,
    **kwargs: Any,
) -> Any:
    """Retry a callable only when it raises a transient network exception."""
    actual_jitter = jitter if jitter is not None else (delay if delay < 1.0 else 1.0)

    def _before_sleep(retry_state: RetryCallState) -> None:
        if logger:
            exc = retry_state.outcome.exception() if retry_state.outcome else None
            sleep_time = (
                retry_state.next_action.sleep if retry_state.next_action else 0.0
            )
            fn_name = getattr(
                function,
                "__qualname__",
                getattr(function, "__name__", str(function)),
            )
            logger.warning(
                "Transient RPC/network error in %s (%s/%s): %s. Retrying in %.2fs.",
                fn_name,
                retry_state.attempt_number,
                retries,
                exc,
                sleep_time,
            )

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(retries),
        wait=wait_exponential_jitter(
            initial=delay, exp_base=backoff, jitter=actual_jitter
        ),
        retry=retry_if_exception(is_transient_network_exception),
        before_sleep=_before_sleep if logger else None,
        reraise=True,
    ):
        with attempt:
            result = function(*args, **kwargs)
            if not asyncio.iscoroutine(result):
                return result
            return await result

    raise AssertionError("retry loop exhausted unexpectedly")
