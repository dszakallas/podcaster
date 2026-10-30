"""Tests for the Jellyfin media server notification handler."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from podcaster.config import (
    DistributionConfig,
    JellyfinNotifierConfig,
    NotifierConfig,
    Ref,
    RsyncDistributionConfig,
    resolve_refs,
)
from podcaster.distribution.base import build_distribution
from podcaster.notifier import JellyfinNotifier, build_notifier
from podcaster.notifier.jellyfin import sync_to_jellyfin


@pytest.mark.anyio
async def test_sync_to_jellyfin_missing_source_raises(tmp_path: Path):
    non_existent = str(tmp_path / "does_not_exist")
    with pytest.raises(FileNotFoundError, match="Source directory .* not found"):
        await sync_to_jellyfin(
            working_dir=non_existent,
            server_url="http://jellyfin:8096",
            api_key="test-api-key",
        )


@pytest.mark.anyio
async def test_sync_to_jellyfin_missing_credentials(tmp_path: Path):
    res = await sync_to_jellyfin(
        working_dir=str(tmp_path),
        server_url=None,
        api_key=None,
    )
    assert res["status"] == "partial_success"
    assert "missing credentials" in res["message"]


@pytest.mark.anyio
async def test_sync_to_jellyfin_with_server_library_path(tmp_path: Path):
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        res = await sync_to_jellyfin(
            working_dir=str(tmp_path),
            server_url="http://jellyfin.local:8096/",
            api_key="test-key",
            server_library_path="/media/podcasts",
        )

        assert res["status"] == "success"
        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        assert url == "http://jellyfin.local:8096/Library/Media/Updated"

        payload = mock_post.call_args.kwargs["json"]
        assert payload == {
            "Updates": [{"Path": "/media/podcasts", "UpdateType": "Created"}]
        }

        headers = mock_post.call_args.kwargs["headers"]
        assert headers["X-Emby-Token"] == "test-key"
        assert 'MediaBrowser Token="test-key"' in headers["Authorization"]


@pytest.mark.anyio
async def test_sync_to_jellyfin_with_item_id(tmp_path: Path):
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        res = await sync_to_jellyfin(
            working_dir=str(tmp_path),
            server_url="http://jellyfin.local:8096",
            token="token-xyz",
            item_id="folder-uuid-1234",
        )

        assert res["status"] == "success"
        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        assert url == "http://jellyfin.local:8096/Items/folder-uuid-1234/Refresh"

        params = mock_post.call_args.kwargs["params"]
        assert params["recursive"] == "true"

        headers = mock_post.call_args.kwargs["headers"]
        assert headers["X-Emby-Token"] == "token-xyz"


@pytest.mark.anyio
async def test_sync_to_jellyfin_global_refresh_fallback(tmp_path: Path):
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        res = await sync_to_jellyfin(
            working_dir=str(tmp_path),
            server_url="http://jellyfin.local:8096",
            api_key="test-key",
        )

        assert res["status"] == "success"
        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        assert url == "http://jellyfin.local:8096/Library/Refresh"


@pytest.mark.anyio
async def test_sync_to_jellyfin_http_error(tmp_path: Path):
    with patch(
        "httpx.AsyncClient.post",
        side_effect=httpx.HTTPStatusError(
            "500 Internal Server Error", request=None, response=None
        ),
    ):
        res = await sync_to_jellyfin(
            working_dir=str(tmp_path),
            server_url="http://jellyfin.local:8096",
            api_key="test-key",
        )

        assert res["status"] == "partial_success"
        assert "Jellyfin rescan failed" in res["message"]


@pytest.mark.anyio
async def test_jellyfin_notifier_class_notify(tmp_path: Path):
    notifier = JellyfinNotifier(
        server_url="http://jellyfin.local:8096",
        api_key="test-key",
        server_library_path="/mnt/podcasts",
        name="my-jellyfin",
    )

    with patch(
        "podcaster.notifier.jellyfin.sync_to_jellyfin", new_callable=AsyncMock
    ) as mock_sync:
        mock_sync.return_value = {"status": "success", "source": str(tmp_path)}

        # 1. From dist_result
        res = await notifier.notify(dist_result={"source": str(tmp_path)})
        assert res["status"] == "success"
        assert mock_sync.call_args.kwargs["working_dir"] == str(tmp_path)

        # 2. From metadata
        res2 = await notifier.notify(metadata={"working_dir": str(tmp_path)})
        assert res2["status"] == "success"
        assert mock_sync.call_args.kwargs["working_dir"] == str(tmp_path)


def test_build_jellyfin_notifier():
    cfg = NotifierConfig(
        jellyfin=JellyfinNotifierConfig(
            server_url="http://jellyfin.local:8096",
            api_key="secret-api-key",
            server_library_path="/podcasts",
            item_id="folder-1",
        )
    )

    notifier = build_notifier(cfg, name="custom-jellyfin")
    assert isinstance(notifier, JellyfinNotifier)
    assert notifier.name == "custom-jellyfin"
    assert notifier.server_url == "http://jellyfin.local:8096"
    assert notifier.api_key == "secret-api-key"
    assert notifier.server_library_path == "/podcasts"
    assert notifier.item_id == "folder-1"


def test_jellyfin_notifier_ref_resolution():
    from tests.test_ref_resolver import _app_config

    jellyfin_cfg = NotifierConfig(
        jellyfin=JellyfinNotifierConfig(
            server_url="http://jellyfin.local:8096",
            api_key="secret-key",
        )
    )
    dist_cfg = DistributionConfig(
        rsync=RsyncDistributionConfig(destination="dest"),
        notifiers=[Ref[NotifierConfig](ref="my-jellyfin")],
    )

    app_config = _app_config(
        notifiers={"my-jellyfin": jellyfin_cfg},
        distributions={"my-dist": dist_cfg},
    )
    resolve_refs(app_config)

    resolved_dist = app_config.distributions["my-dist"]
    resolved_notifier = resolved_dist.notifiers[0]
    assert isinstance(resolved_notifier, NotifierConfig)

    notifier = build_notifier(resolved_notifier)
    assert isinstance(notifier, JellyfinNotifier)
    assert notifier.name == "my-jellyfin"

    dist = build_distribution(resolved_dist)
    assert dist.name == "my-dist"
    assert len(dist.notifiers) == 1
    assert dist.notifiers[0].name == "my-jellyfin"
