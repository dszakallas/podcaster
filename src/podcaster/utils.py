import contextlib
import functools
import inspect
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml


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
    from .config import AppConfig

    config_path = Path("podcaster.yaml")
    data = {}
    if config_path.exists():
        with open(config_path, "r") as f:
            data = yaml.safe_load(f) or {}
    return AppConfig.model_validate(data)


def get_storage_path() -> str:
    """Get the NotebookLM storage state path."""
    storage_path = os.environ.get("NOTEBOOKLM_STORAGE_STATE")
    if not storage_path:
        home = os.environ.get("NOTEBOOKLM_HOME", "~/.notebooklm")
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


DEFAULT_PODCAST_DIR = "podcasts"


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


def rsync_dir(src: str, dst: str):
    """Rsyncs a directory to a destination, creating parent if needed."""
    import subprocess

    os.makedirs(os.path.dirname(dst.rstrip("/")), exist_ok=True)
    # --mkpath is only in newer rsync, so we use mkdir -p via python first
    # We want to rsync the contents of the notebook folder to a folder of the same name at dst
    cmd = [
        "rsync",
        "-avz",
        "--include=*/",
        "--include=*.m4a",
        "--include=*.lrc",
        "--exclude=*",
        src.rstrip("/") + "/",
        dst.rstrip("/") + "/",
    ]
    subprocess.run(cmd, check=True)


def rclone_copy_dir(src: str, dst: str):
    """Copies a directory using rclone, creating parent if needed."""
    import subprocess

    # rclone handles creating parents usually, but let's be consistent
    # Note: dst might be a remote like 'remote:path/to/dir'
    cmd = ["rclone", "copy", "--include", "*.m4a", "--include", "*.lrc", src, dst]
    subprocess.run(cmd, check=True)


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


async def retry_rpc(
    coro_or_func,
    *args,
    retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    logger: Optional[logging.Logger] = None,
    **kwargs,
):
    """Retries a coroutine or async function call with exponential backoff on transient errors."""
    import asyncio

    import httpx
    from notebooklm.exceptions import NetworkError, RPCError

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
            class_name = e.__class__.__name__
            is_transient = (
                isinstance(e, (NetworkError, RPCError, httpx.HTTPError))
                or "APIError" in class_name
                or "ServerError" in class_name
            )
            if not is_transient:
                raise e

            if "NotFound" in class_name:
                raise e
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                raise e
            if "APIError" in class_name or "ServerError" in class_name:
                code = getattr(e, "code", None)
                if (
                    code is not None
                    and isinstance(code, int)
                    and code < 500
                    and code != 429
                ):
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


class RetryingNotebookLMClient:
    @classmethod
    async def from_storage(cls, storage_path, timeout=120.0, logger=None):
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
        return await self._client.__aexit__(exc_type, exc_val, exc_tb)
