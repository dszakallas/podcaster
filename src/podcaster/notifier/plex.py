import logging
import os
from typing import Any

import httpx

from .base import Notifier, register_notifier

logger = logging.getLogger(__name__)


async def sync_to_plex(
    working_dir: str,
    plex_section_id: int | str,
    plex_server_url: str | None = None,
    plex_token: str | None = None,
    server_library_path: str | None = None,
) -> dict:

    if not os.path.exists(working_dir):
        raise FileNotFoundError(f"Source directory {working_dir} not found.")

    if not plex_server_url or not plex_token:
        logger.warning("PLEX_SERVER_URL or PLEX_TOKEN not found. Skipping API rescan.")
        return {
            "source": working_dir,
            "status": "partial_success",
            "message": "Plex rescan skipped due to missing credentials.",
        }

    base_url = plex_server_url.rstrip("/")
    refresh_url = f"{base_url}/library/sections/{plex_section_id}/refresh"
    api_path = server_library_path or os.path.realpath(working_dir)

    params = {"path": api_path, "X-Plex-Token": plex_token, "force": "1"}
    headers = {"X-Plex-Token": plex_token, "Accept": "application/json"}

    logger.debug(f"Triggering Plex rescan via API: {refresh_url} (path: {api_path})")

    try:
        async with httpx.AsyncClient() as client_http:
            response = await client_http.get(
                refresh_url, params=params, headers=headers, timeout=10.0
            )
            response.raise_for_status()
        logger.debug("Plex API rescan triggered successfully.")
    except Exception as e:
        logger.debug(f"Plex API rescan failed: {e}")
        return {
            "source": working_dir,
            "status": "partial_success",
            "message": f"Plex rescan failed: {e}",
        }

    return {
        "source": working_dir,
        "status": "success",
    }


class PlexNotifier(Notifier):
    """Plex library refresh notifier implementation."""

    def __init__(
        self,
        section_id: int | str,
        server_library_path: str | None = None,
        server_url: str | None = None,
        token: str | None = None,
        name: str | None = None,
    ):
        self.section_id = section_id
        self.server_library_path = server_library_path
        self.server_url = server_url
        self.token = token
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
        return await sync_to_plex(
            working_dir=working_dir,
            plex_section_id=self.section_id,
            plex_server_url=self.server_url,
            plex_token=self.token,
            server_library_path=self.server_library_path,
        )


def _build_plex_notifier(cfg: Any, name: str | None = None) -> Notifier:
    assert cfg.plex is not None
    return PlexNotifier(
        section_id=cfg.plex.section_id,
        server_library_path=cfg.plex.server_library_path,
        server_url=cfg.plex.server_url,
        token=cfg.plex.token,
        name=name,
    )


register_notifier("plex", _build_plex_notifier)
