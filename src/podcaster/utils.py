import contextlib
import functools
import inspect
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml


def get_env_var(
    kind: str,
    name: Optional[str],
    var_name: str,
    default: Optional[str] = None,
) -> Optional[str]:
    """Resolves an environment variable with a component-scoped fallback mechanism.

    Checks:
    1. <KIND>_<UPPERCASED_UNDERSCORED_NAME>_<VAR_NAME> (if name is provided)
    2. <VAR_NAME>
    3. default
    """
    if name:
        sanitized_name = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()
        scoped_var = f"{kind.upper()}_{sanitized_name}_{var_name}"
        val = os.environ.get(scoped_var)
        if val is not None and val != "":
            return val
    val = os.environ.get(var_name)
    if val is not None and val != "":
        return val
    return default


class StructuredFormatter(logging.Formatter):
    def format(self, record):
        message = super().format(record)
        standard_attrs = {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
            "taskName",
        }
        extra = {k: v for k, v in record.__dict__.items() if k not in standard_attrs}
        if extra:

            def default_serializer(obj):
                if isinstance(obj, Path):
                    return str(obj)
                try:
                    return str(obj)
                except Exception:
                    return repr(obj)

            try:
                extra_str = json.dumps(extra, default=default_serializer)
                message = f"{message} - {extra_str}"
            except Exception:
                pass
        return message


def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    formatter = StructuredFormatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)

    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)


@contextlib.asynccontextmanager
async def log_task(task_name: str, logger: logging.Logger, **parameters):
    clean_params = {k: v for k, v in parameters.items() if v is not None}
    logger.info(
        f"Task '{task_name}' started",
        extra={"task": task_name, "status": "started", "parameters": clean_params},
    )
    start_time = time.monotonic()
    try:
        yield
        duration = time.monotonic() - start_time
        logger.info(
            f"Task '{task_name}' completed successfully in {duration:.2f}s",
            extra={
                "task": task_name,
                "status": "completed",
                "duration_seconds": round(duration, 3),
                "parameters": clean_params,
            },
        )
    except Exception as e:
        duration = time.monotonic() - start_time
        logger.error(
            f"Task '{task_name}' failed after {duration:.2f}s with error: {e}",
            exc_info=True,
            extra={
                "task": task_name,
                "status": "failed",
                "duration_seconds": round(duration, 3),
                "parameters": clean_params,
                "error": str(e),
            },
        )
        raise


def task(task_name: str, logger: logging.Logger):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            sig = inspect.signature(func)
            bound = sig.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            params = {
                k: v
                for k, v in bound.arguments.items()
                if not k.startswith("_")
                and k not in ("logger", "client")
                and v is not None
            }

            logger.info(
                f"Task '{task_name}' started",
                extra={"task": task_name, "status": "started", "parameters": params},
            )
            start_time = time.monotonic()
            try:
                result = await func(*args, **kwargs)
                duration = time.monotonic() - start_time
                logger.info(
                    f"Task '{task_name}' completed successfully in {duration:.2f}s",
                    extra={
                        "task": task_name,
                        "status": "completed",
                        "duration_seconds": round(duration, 3),
                        "parameters": params,
                    },
                )
                return result
            except Exception as e:
                duration = time.monotonic() - start_time
                logger.error(
                    f"Task '{task_name}' failed after {duration:.2f}s with error: {e}",
                    exc_info=True,
                    extra={
                        "task": task_name,
                        "status": "failed",
                        "duration_seconds": round(duration, 3),
                        "parameters": params,
                        "error": str(e),
                    },
                )
                raise

        return wrapper

    return decorator


def load_config():
    from .config import AppConfig, resolve_refs

    config_path = Path("podcaster.yaml")
    data = {}
    if config_path.exists():
        with open(config_path, "r") as f:
            data = yaml.safe_load(f) or {}
    config = AppConfig.model_validate(data)
    return resolve_refs(config)


def get_storage_path() -> str:
    """Get the NotebookLM storage state path."""
    storage_path = os.environ.get("NOTEBOOKLM_STORAGE_STATE")
    if not storage_path:
        home = os.environ.get("NOTEBOOKLM_HOME", "~/.notebooklm")
        profile_path = os.path.expanduser(
            os.path.join(home, "profiles", "default", "storage_state.json")
        )
        if os.path.exists(profile_path):
            storage_path = profile_path
        else:
            storage_path = os.path.expanduser(os.path.join(home, "storage_state.json"))
    return storage_path


def sanitize(s: str) -> str:
    """Sanitize string for filenames."""
    return "".join([c if c.isalnum() else "_" for c in s])


def get_notebook_dir_name(
    title: str, notebook_id: str, created_at: Optional[datetime] = None
) -> str:
    """Get the standardized directory name for a notebook with ID suffix."""
    safe_title = sanitize(title)
    if created_at:
        date_str = created_at.strftime("%Y-%m-%d")
        name = f"{date_str} - {safe_title}"
    else:
        name = safe_title
    return f"{name} [nlm_{notebook_id}]"


def find_notebook_dir(base_dir: str, notebook_id: str) -> Optional[str]:
    """Find an existing notebook directory by matching the [nlm_id] suffix."""
    if not os.path.exists(base_dir):
        return None

    suffix = f"[nlm_{notebook_id}]"
    for entry in os.listdir(base_dir):
        full_path = os.path.join(base_dir, entry)
        if os.path.isdir(full_path) and entry.endswith(suffix):
            return entry
    return None


def resolve_notebook_dir_path(notebook_id: str, podcast_dir: str) -> Path:
    """Resolve the local storage directory path for a notebook ID, raising ValueError if not found."""
    notebook_dir_name = find_notebook_dir(podcast_dir, notebook_id)
    if not notebook_dir_name:
        raise ValueError(f"Could not find directory for notebook ID: {notebook_id}")
    return Path(podcast_dir) / notebook_dir_name


def get_or_create_notebook_dir(
    base_dir: str, notebook_id: str, title: str, created_at: Optional[datetime] = None
) -> str:
    """Find or create a standardized notebook directory."""
    notebook_dir_name = find_notebook_dir(base_dir, notebook_id)
    if not notebook_dir_name:
        notebook_dir_name = get_notebook_dir_name(title, notebook_id, created_at)

    notebook_dir = os.path.join(base_dir, notebook_dir_name)
    os.makedirs(notebook_dir, exist_ok=True)
    return notebook_dir


# Presets expressed as canonical duration strings.
_PRESET_DURATIONS: dict[str, str] = {
    "short": "10 minutes",
    "default": "20 minutes",
    "long": "30 minutes",
}

_DURATION_RE = r"""(?x)
    ^\s*
    (?:(\d+)\s*h(?:ours?)?)?\s*   # optional hours
    (?:(\d+)\s*m(?:in(?:utes?)?)?) # required minutes
    \s*$
"""


def parse_duration_minutes(duration: str) -> Optional[int]:
    """Parse a human-readable duration string into total minutes.

    Accepts formats like '10 minutes', '1 hour 30 minutes', '45m', '1h 15min'.
    Returns None if unparseable.
    """
    import re

    m = re.match(_DURATION_RE, duration.strip(), re.VERBOSE | re.IGNORECASE)
    if not m:
        return None
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    return hours * 60 + minutes


def resolve_duration(length: str) -> str:
    """Normalise a length value to a duration string.

    Accepts a preset name ('short', 'default', 'long') or any parseable
    duration string ('23 minutes', '1 hour 5 minutes', …).  Returns the
    canonical duration string, or raises ValueError for unrecognised input.
    """
    if length in _PRESET_DURATIONS:
        return _PRESET_DURATIONS[length]
    if parse_duration_minutes(length) is not None:
        return length
    raise ValueError(
        f"Invalid length {length!r}: expected a preset ('short', 'default', 'long') "
        "or a duration string (e.g. '23 minutes', '1 hour 10 minutes')."
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


def is_transient_network_exception(e: Exception) -> bool:
    """Explicit whitelist for transient HTTP/gRPC/TCP network exceptions that are safe to retry.
    Closed-by-default: non-network errors (400 Bad Request, 401, 403, 404, client bugs) return False.
    """
    import httpx
    from notebooklm.exceptions import NetworkError

    if isinstance(e, (ConnectionError, TimeoutError, OSError, NetworkError)):
        return True

    if isinstance(
        e,
        (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.ConnectError,
            httpx.ProtocolError,
        ),
    ):
        return True

    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code in RETRYABLE_STATUS_CODES

    class_name = e.__class__.__name__
    if class_name in RETRYABLE_CLASS_NAMES:
        return True

    code = getattr(e, "code", None)
    if callable(code):
        try:
            code = code()
        except Exception:
            code = None

    if hasattr(code, "name"):
        if str(getattr(code, "name", "")) in RETRYABLE_GRPC_STATUSES:
            return True

    if isinstance(code, int):
        return code in RETRYABLE_STATUS_CODES

    return False


async def retry_rpc(
    coro_or_func,
    *args,
    retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    logger: Optional[logging.Logger] = None,
    **kwargs,
):
    """Retries a coroutine or async function call with exponential backoff ONLY on transient network errors."""
    import asyncio

    current_delay = delay
    for attempt in range(1, retries + 1):
        try:
            if callable(coro_or_func):
                res = coro_or_func(*args, **kwargs)
                if asyncio.iscoroutine(res):
                    return await res
                return res
            else:
                raise ValueError(
                    "retry_rpc expects an async callable/function, not a coroutine object"
                )
        except Exception as e:
            if not is_transient_network_exception(e):
                raise e

            if attempt == retries:
                raise e

            if logger:
                logger.warning(
                    f"Transient RPC/Network error (attempt {attempt}/{retries}): {e}. "
                    f"Retrying in {current_delay:.2f} seconds..."
                )
            await asyncio.sleep(current_delay)
            current_delay *= backoff


class RetryingResourceWrapper:
    def __init__(self, resource, logger=None):
        self._resource = resource
        self._logger = logger or logging.getLogger("notebooklm.client")

    def __getattr__(self, name):
        attr = getattr(self._resource, name)
        if callable(attr):
            is_safe = (
                name in ("get", "list", "poll", "wait_until_ready", "download", "ask")
                or name.startswith("get_")
                or name.startswith("list_")
                or name.startswith("poll_")
                or name.startswith("download_")
                or "_get" in name
                or "_list" in name
                or "_poll" in name
                or "_download" in name
            )
            if is_safe:

                @functools.wraps(attr)
                async def wrapped(*args, **kwargs):
                    return await retry_rpc(attr, *args, logger=self._logger, **kwargs)

                return wrapped
        return attr


DEFAULT_CLIENT_TIMEOUT = 120.0


class RetryingNotebookLMClient:
    notebooks: Any
    sources: Any
    artifacts: Any
    research: Any
    chat: Any

    @classmethod
    async def from_storage(
        cls, storage_path, timeout=DEFAULT_CLIENT_TIMEOUT, logger=None
    ):
        from notebooklm import NotebookLMClient

        client = await NotebookLMClient.from_storage(storage_path, timeout=timeout)
        return cls(client, logger=logger)

    def __init__(self, client, logger=None):
        self._client = client
        self._logger = logger or logging.getLogger("notebooklm.client")

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if name in ("notebooks", "sources", "artifacts", "research", "chat"):
            return RetryingResourceWrapper(attr, logger=self._logger)
        return attr

    async def __aenter__(self):
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            return await self._client.__aexit__(exc_type, exc_val, exc_tb)
        except Exception as e:
            if exc_type is not None:
                self._logger.debug(
                    f"Suppressed exception raised during client close(): {e}"
                )
                return False
            raise


@contextlib.asynccontextmanager
async def get_notebooklm_client(
    timeout: float = DEFAULT_CLIENT_TIMEOUT, logger: Optional[logging.Logger] = None
):
    """Async context manager for creating and managing a RetryingNotebookLMClient."""
    storage_path = get_storage_path()
    async with await RetryingNotebookLMClient.from_storage(
        storage_path, timeout=timeout, logger=logger
    ) as client:
        yield client
