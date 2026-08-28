"""DBOS initialization, lifecycle, and workflow helpers."""

import asyncio
import logging
import os
from typing import Any

from ..config import DBOSConfig as PodcasterDBOSConfig

GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 15


def configure_dbos_logging() -> None:
    """Route DBOS warnings and errors through Podcaster's logging configuration."""
    dbos_logger = logging.getLogger("dbos")
    dbos_logger.handlers.clear()
    dbos_logger.propagate = True
    dbos_logger.setLevel(logging.WARNING)


def ensure_dbos_initialized(dbos_config: PodcasterDBOSConfig) -> None:
    """Initialize DBOS with the supplied validated database configuration."""
    from dbos import DBOS, DBOSConfig

    if getattr(DBOS, "_dbos_global_instance", None) is not None:
        return

    db_url = None
    if dbos_config.engine == "sqlite":
        full_path = os.path.expanduser(dbos_config.sqlite_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        db_url = f"sqlite:///{full_path}"
    elif dbos_config.postgres_url:
        db_url = dbos_config.postgres_url

    dbos_cfg = DBOSConfig(name="podcaster", log_level="WARNING")
    if db_url:
        dbos_cfg["system_database_url"] = db_url
    try:
        DBOS(config=dbos_cfg)
        configure_dbos_logging()
        DBOS.launch()
    except Exception as exc:
        raise RuntimeError("Failed to initialize DBOS") from exc


async def wait_for_workflow_result(workflow_id: str) -> Any:
    """Wait for a DBOS workflow, cancelling it if the CLI is interrupted."""
    from dbos import DBOS

    try:
        return await DBOS.get_result_async(workflow_id)
    except asyncio.CancelledError:
        logging.getLogger(__name__).info(
            "Graceful shutdown initiated; cancelling workflow %s", workflow_id
        )
        await DBOS.cancel_workflow_async(workflow_id, cancel_children=True)
        raise


def shutdown_dbos() -> None:
    """Wait briefly for local workflows to exit before stopping DBOS resources."""
    from dbos import DBOS

    DBOS.destroy(workflow_completion_timeout_sec=GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS)


def assert_workflow_version(
    workflow_id: str,
    workflow_version: str | None,
    current_version: str,
) -> None:
    """Reject workflow resumption when the registered source versions differ."""
    if workflow_version != current_version:
        raise ValueError(
            f"Workflow '{workflow_id}' was created with DBOS application version "
            f"'{workflow_version}', but this process runs '{current_version}'. "
            "It cannot be resumed safely with different workflow source code."
        )
