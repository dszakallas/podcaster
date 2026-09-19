import logging
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from notebooklm.exceptions import NetworkError

from podcaster.config import NotebookLMConfig
from podcaster.utils.notebooklm import (
    RetryingNotebookLMClient,
    _get_storage_path,
    _RetryingResourceWrapper,
)
from podcaster.utils.retry import is_transient_network_exception, retry_rpc


class ServiceUnavailable(Exception):
    pass


class NotFoundError(Exception):
    pass


def test_is_transient_network_exception():
    # Built-in network/timeout errors
    assert is_transient_network_exception(ConnectionError("refused"))
    assert is_transient_network_exception(TimeoutError("timed out"))
    assert is_transient_network_exception(OSError("os error"))

    # NotebookLM NetworkError
    assert is_transient_network_exception(NetworkError("notebooklm net err"))

    # HTTPX errors
    assert is_transient_network_exception(httpx.ConnectError("connect err"))
    assert is_transient_network_exception(
        httpx.TimeoutException("timeout", request=MagicMock())
    )

    # HTTPStatusError
    req = httpx.Request("GET", "https://example.com")
    resp_503 = httpx.Response(503, request=req)
    assert is_transient_network_exception(
        httpx.HTTPStatusError("503", request=req, response=resp_503)
    )

    resp_404 = httpx.Response(404, request=req)
    assert not is_transient_network_exception(
        httpx.HTTPStatusError("404", request=req, response=resp_404)
    )

    # Class name match
    assert is_transient_network_exception(ServiceUnavailable("service unavailable"))

    # gRPC status mock
    class GrpcStatusError(Exception):
        def code(self):
            class Code:
                name = "UNAVAILABLE"

            return Code()

    assert is_transient_network_exception(GrpcStatusError("grpc unavailable"))

    # Non-transient errors
    assert not is_transient_network_exception(ValueError("invalid value"))
    assert not is_transient_network_exception(KeyError("missing key"))
    assert not is_transient_network_exception(NotFoundError("not found"))


@pytest.mark.anyio
async def test_retry_rpc_succeeds_first_attempt():
    func = AsyncMock(return_value="success")
    result = await retry_rpc(func, "arg1", delay=0.01)
    assert result == "success"
    func.assert_awaited_once_with("arg1")


@pytest.mark.anyio
async def test_retry_rpc_retries_on_transient_and_succeeds():
    attempts = []

    async def flaky_call():
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("temporary drop")
        return "finally succeeded"

    result = await retry_rpc(flaky_call, retries=3, delay=0.001, backoff=1.0)
    assert result == "finally succeeded"
    assert len(attempts) == 3


@pytest.mark.anyio
async def test_retry_rpc_fails_fast_on_non_transient():
    func = AsyncMock(side_effect=ValueError("bad parameter"))
    with pytest.raises(ValueError, match="bad parameter"):
        await retry_rpc(func, retries=3, delay=0.001)
    # Should not retry
    assert func.await_count == 1


@pytest.mark.anyio
async def test_retry_rpc_exhausts_retries():
    func = AsyncMock(side_effect=TimeoutError("persistent timeout"))
    with pytest.raises(TimeoutError, match="persistent timeout"):
        await retry_rpc(func, retries=3, delay=0.001, backoff=1.0)
    assert func.await_count == 3


@pytest.mark.anyio
async def test_retry_rpc_logs_transient_attempts():
    attempts = 0

    async def flaky_call():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("intermittent failure")
        return "success"

    logger = logging.getLogger("test_retry_rpc")
    with patch.object(logger, "warning") as mock_warning:
        result = await retry_rpc(
            flaky_call, retries=3, delay=0.001, backoff=1.0, logger=logger
        )
        assert result == "success"
        assert attempts == 2
        mock_warning.assert_called_once()
        assert "Transient RPC/network error" in mock_warning.call_args[0][0]


def test_get_storage_path_explicit():
    cfg = NotebookLMConfig(storage_state="/explicit/path/storage.json")
    assert _get_storage_path(cfg) == "/explicit/path/storage.json"


def test_get_storage_path_profile_exists(tmp_path):
    fake_home = tmp_path / "nlm_home"
    profile_dir = fake_home / "profiles" / "work"
    profile_dir.mkdir(parents=True)
    profile_state = profile_dir / "storage_state.json"
    profile_state.write_text("{}", encoding="utf-8")

    cfg = NotebookLMConfig(home=str(fake_home), profile="work")
    assert _get_storage_path(cfg) == str(profile_state)


def test_get_storage_path_fallback_home(tmp_path):
    fake_home = tmp_path / "nlm_home"
    cfg = NotebookLMConfig(home=str(fake_home), profile="missing_profile")
    expected = str(fake_home / "storage_state.json")
    assert _get_storage_path(cfg) == expected


@pytest.mark.anyio
async def test_retrying_resource_wrapper_wraps_safe_methods():
    class DummyResource:
        async def get(self, item_id: str):
            return f"item_{item_id}"

        async def delete(self, item_id: str):
            return f"deleted_{item_id}"

        unrelated_attr = 42

    resource = DummyResource()
    wrapper = _RetryingResourceWrapper(resource)

    # Safe method is wrapped
    assert callable(wrapper.get)
    result = await cast(Any, wrapper.get)("123")
    assert result == "item_123"

    # Unsafe / non-retryable method (like delete) is untouched
    assert wrapper.delete == resource.delete
    assert wrapper.unrelated_attr == 42


@pytest.mark.anyio
async def test_retrying_client_attribute_delegation_and_lifecycle():
    class FakeUnderlyingClient:
        def __init__(self):
            self.notebooks = MagicMock()
            self.sources = MagicMock()
            self.artifacts = MagicMock()
            self.research = MagicMock()
            self.chat = MagicMock()
            self.custom_property = "custom"
            self.entered = False
            self.exited = False

        async def __aenter__(self):
            self.entered = True
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            self.exited = True
            if exc_type is not None:
                raise RuntimeError("client close error")

    underlying = FakeUnderlyingClient()
    retrying_client = RetryingNotebookLMClient(underlying)

    # Resources are wrapped
    assert isinstance(retrying_client.notebooks, _RetryingResourceWrapper)
    assert isinstance(retrying_client.chat, _RetryingResourceWrapper)
    # Non-resource attributes are returned directly
    assert retrying_client.custom_property == "custom"

    # Test context manager suppressing close error when primary error occurred
    logger = logging.getLogger("test_logger")
    retrying_client = RetryingNotebookLMClient(underlying, logger=logger)
    with patch.object(logger, "debug") as mock_debug:
        # Context with exception
        try:
            async with retrying_client:
                raise ValueError("primary error in block")
        except ValueError as e:
            assert str(e) == "primary error in block"

        mock_debug.assert_called_once()
        assert "Suppressed exception" in mock_debug.call_args[0][0]
