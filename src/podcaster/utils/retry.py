"""Transient-network detection and retry helpers."""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import httpx
from notebooklm.exceptions import NetworkError

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


def is_transient_network_exception(error: Exception) -> bool:
    """Return whether an exception is safe to retry as a transient network failure."""
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
    logger: logging.Logger | None = None,
    **kwargs: Any,
) -> Any:
    """Retry a callable only when it raises a transient network exception."""
    current_delay = delay
    for attempt in range(1, retries + 1):
        try:
            result = function(*args, **kwargs)
            return await result if asyncio.iscoroutine(result) else result
        except Exception as exc:
            if not is_transient_network_exception(exc) or attempt == retries:
                raise
            if logger:
                logger.warning(
                    "Transient RPC/network error (%s/%s): %s. Retrying in %.2fs.",
                    attempt,
                    retries,
                    exc,
                    current_delay,
                )
            await asyncio.sleep(current_delay)
            current_delay *= backoff

    raise AssertionError("retry loop exhausted unexpectedly")
