"""Jellyfin media server library notification and rescan handler."""

import logging
import os
from typing import Any

import httpx

from .base import Notifier, register_notifier

logger = logging.getLogger(__name__)


async def sync_to_jellyfin(
    working_dir: str,
    server_url: str | None = None,
    token: str | None = None,
    api_key: str | None = None,
    server_library_path: str | None = None,
    item_id: str | None = None,
) -> dict:
    if not os.path.exists(working_dir):
        raise FileNotFoundError(f"Source directory {working_dir} not found.")

    effective_token = token or api_key
    if not server_url or not effective_token:
        logger.warning(
            "JELLYFIN_SERVER_URL or JELLYFIN_API_KEY/JELLYFIN_TOKEN not found. Skipping API rescan."
        )
        return {
            "source": working_dir,
            "status": "partial_success",
            "message": "Jellyfin rescan skipped due to missing credentials.",
        }

    base_url = server_url.rstrip("/")
    headers = {
        "X-Emby-Token": effective_token,
        "Authorization": f'MediaBrowser Token="{effective_token}"',
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient() as client_http:
            if server_library_path:
                refresh_url = f"{base_url}/Library/Media/Updated"
                payload = {
                    "Updates": [{"Path": server_library_path, "UpdateType": "Created"}]
                }
                logger.debug(
                    f"Triggering Jellyfin media update via API: {refresh_url} (path: {server_library_path})"
                )
                response = await client_http.post(
                    refresh_url, json=payload, headers=headers, timeout=10.0
                )
            elif item_id:
                refresh_url = f"{base_url}/Items/{item_id}/Refresh"
                params = {
                    "recursive": "true",
                    "metadataRefreshMode": "Default",
                    "imageRefreshMode": "Default",
                }
                logger.debug(
                    f"Triggering Jellyfin item refresh via API: {refresh_url} (item: {item_id})"
                )
                response = await client_http.post(
                    refresh_url, params=params, headers=headers, timeout=10.0
                )
            else:
                refresh_url = f"{base_url}/Library/Refresh"
                logger.debug(
                    f"Triggering Jellyfin global library refresh via API: {refresh_url}"
                )
                response = await client_http.post(
                    refresh_url, headers=headers, timeout=10.0
                )

            response.raise_for_status()
        logger.debug("Jellyfin API rescan triggered successfully.")
    except Exception as e:
        logger.debug(f"Jellyfin API rescan failed: {e}")
        return {
            "source": working_dir,
            "status": "partial_success",
            "message": f"Jellyfin rescan failed: {e}",
        }

    return {
        "source": working_dir,
        "status": "success",
    }


class JellyfinNotifier(Notifier):
    """Jellyfin library refresh notifier implementation."""

    def __init__(
        self,
        server_library_path: str | None = None,
        server_url: str | None = None,
        token: str | None = None,
        api_key: str | None = None,
        item_id: str | None = None,
        name: str | None = None,
    ):
        self.server_library_path = server_library_path
        self.server_url = server_url
        self.token = token
        self.api_key = api_key
        self.item_id = item_id
        self.name = name

    async def notify(
        self,
        metadata: dict | None = None,
        dist_result: dict | None = None,
    ) -> dict:
        working_dir = (
            (dist_result.get("source") if dist_result else None)
            or (metadata.get("working_dir") if metadata else None)
            or "."
        )
        return await sync_to_jellyfin(
            working_dir=working_dir,
            server_url=self.server_url,
            token=self.token,
            api_key=self.api_key,
            server_library_path=self.server_library_path,
            item_id=self.item_id,
        )


def _build_jellyfin_notifier(cfg: Any, name: str | None = None) -> Notifier:
    assert cfg.jellyfin is not None
    return JellyfinNotifier(
        server_library_path=cfg.jellyfin.server_library_path,
        server_url=cfg.jellyfin.server_url,
        token=cfg.jellyfin.token,
        api_key=cfg.jellyfin.api_key,
        item_id=cfg.jellyfin.item_id,
        name=name,
    )


register_notifier("jellyfin", _build_jellyfin_notifier)
