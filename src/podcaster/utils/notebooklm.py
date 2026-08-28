"""NotebookLM client construction and retrying resource wrappers."""

import contextlib
import functools
import logging
import os
from typing import Any, AsyncGenerator

from ..config import NotebookLMConfig
from .retry import retry_rpc

DEFAULT_CLIENT_TIMEOUT = 120.0
_RETRYABLE_RESOURCE_METHODS = {
    "get",
    "list",
    "poll",
    "wait_until_ready",
    "download",
    "ask",
}


def _get_storage_path(config: NotebookLMConfig) -> str:
    """Resolve the NotebookLM storage state path from validated configuration."""
    storage_path = config.storage_state
    if storage_path:
        return storage_path
    notebooklm_home = config.home or "~/.notebooklm"
    profile = config.profile or "default"
    profile_path = os.path.expanduser(
        os.path.join(notebooklm_home, "profiles", profile, "storage_state.json")
    )
    if os.path.exists(profile_path):
        return profile_path
    return os.path.expanduser(os.path.join(notebooklm_home, "storage_state.json"))


class _RetryingResourceWrapper:
    """Apply retry policy to safe NotebookLM resource methods."""

    def __init__(self, resource: Any, logger: logging.Logger | None = None):
        self._resource = resource
        self._logger = logger or logging.getLogger("notebooklm.client")

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._resource, name)
        is_safe = name in _RETRYABLE_RESOURCE_METHODS or any(
            marker in name
            for marker in (
                "get_",
                "list_",
                "poll_",
                "download_",
                "_get",
                "_list",
                "_poll",
                "_download",
            )
        )
        if not callable(attribute) or not is_safe:
            return attribute

        @functools.wraps(attribute)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            return await retry_rpc(attribute, *args, logger=self._logger, **kwargs)

        return wrapped


class RetryingNotebookLMClient:
    """NotebookLM client adapter that retries safe resource operations."""

    notebooks: Any
    sources: Any
    artifacts: Any
    research: Any
    chat: Any

    @classmethod
    @contextlib.asynccontextmanager
    async def from_storage(
        cls,
        storage_path: str,
        timeout: float = DEFAULT_CLIENT_TIMEOUT,
        logger: logging.Logger | None = None,
    ) -> AsyncGenerator["RetryingNotebookLMClient", None]:
        """Create a retrying client from a NotebookLM storage state file."""
        from notebooklm import NotebookLMClient

        async with NotebookLMClient.from_storage(
            storage_path, timeout=timeout
        ) as client:
            yield cls(client, logger=logger)

    def __init__(self, client: Any, logger: logging.Logger | None = None):
        self._client = client
        self._logger = logger or logging.getLogger("notebooklm.client")

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._client, name)
        if name in ("notebooks", "sources", "artifacts", "research", "chat"):
            return _RetryingResourceWrapper(attribute, logger=self._logger)
        return attribute

    async def __aenter__(self) -> "RetryingNotebookLMClient":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool | None:
        try:
            return await self._client.__aexit__(exc_type, exc_val, exc_tb)
        except Exception as exc:
            if exc_type is None:
                raise
            self._logger.debug(
                "Suppressed exception raised during client close(): %s", exc
            )
            return False


@contextlib.asynccontextmanager
async def get_notebooklm_client(
    config: NotebookLMConfig,
    timeout: float = DEFAULT_CLIENT_TIMEOUT,
    logger: logging.Logger | None = None,
):
    """Create and close a retrying NotebookLM client."""
    async with RetryingNotebookLMClient.from_storage(
        _get_storage_path(config), timeout=timeout, logger=logger
    ) as client:
        yield client
