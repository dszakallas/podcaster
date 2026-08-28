"""Structured logging and task-lifecycle helpers."""

import contextlib
import functools
import inspect
import json
import logging
import sys
import time
from pathlib import Path


class StructuredFormatter(logging.Formatter):
    """Format log extras as a JSON suffix."""

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
        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in standard_attrs
        }
        if not extra:
            return message

        def default_serializer(value):
            if isinstance(value, Path):
                return str(value)
            try:
                return str(value)
            except Exception:
                return repr(value)

        try:
            return f"{message} - {json.dumps(extra, default=default_serializer)}"
        except Exception:
            return message


def setup_logging(verbose: bool) -> None:
    """Configure the root logger for Podcaster commands."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        StructuredFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)


@contextlib.asynccontextmanager
async def log_task(task_name: str, logger: logging.Logger, **parameters):
    """Log a task's start, completion, and failure with elapsed time."""
    clean_params = {
        key: value for key, value in parameters.items() if value is not None
    }
    logger.info(
        "Task '%s' started",
        task_name,
        extra={"task": task_name, "status": "started", "parameters": clean_params},
    )
    start_time = time.monotonic()
    try:
        yield
    except Exception as exc:
        duration = time.monotonic() - start_time
        logger.error(
            "Task '%s' failed after %.2fs with error: %s",
            task_name,
            duration,
            exc,
            exc_info=True,
            extra={
                "task": task_name,
                "status": "failed",
                "duration_seconds": round(duration, 3),
                "parameters": clean_params,
                "error": str(exc),
            },
        )
        raise
    else:
        duration = time.monotonic() - start_time
        logger.info(
            "Task '%s' completed successfully in %.2fs",
            task_name,
            duration,
            extra={
                "task": task_name,
                "status": "completed",
                "duration_seconds": round(duration, 3),
                "parameters": clean_params,
            },
        )


def task(task_name: str, logger: logging.Logger):
    """Decorate an async function with task lifecycle logging."""

    def decorator(function):
        @functools.wraps(function)
        async def wrapper(*args, **kwargs):
            signature = inspect.signature(function)
            bound = signature.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            parameters = {
                key: value
                for key, value in bound.arguments.items()
                if not key.startswith("_")
                and key not in ("logger", "client")
                and value is not None
            }
            async with log_task(task_name, logger, **parameters):
                return await function(*args, **kwargs)

        return wrapper

    return decorator
