import logging
import os
from typing import Optional, Union

import httpx

from ..utils import find_notebook_dir, get_env_var, load_config
from .base import Notifier

logger = logging.getLogger(__name__)


async def sync_to_plex(
    notebook_id: str,
    plex_section_id: Union[int, str],
    podcast_dir: Optional[str] = None,
    plex_server_url: Optional[str] = None,
    plex_token: Optional[str] = None,
    server_library_path: Optional[str] = None,
    name: Optional[str] = None,
) -> dict:
    config = load_config()
    if not podcast_dir:
        podcast_dir = config.podcast_dir

    notebook_dir_name = find_notebook_dir(podcast_dir, notebook_id)
    if not notebook_dir_name:
        raise FileNotFoundError(
            f"Notebook directory not found for notebook ID: {notebook_id}"
        )

    source_dir = os.path.join(podcast_dir, notebook_dir_name)

    if not os.path.exists(source_dir):
        raise FileNotFoundError(f"Source directory {source_dir} not found.")

    plex_server_url = plex_server_url or get_env_var(
        "NOTIFIER", name, "PLEX_SERVER_URL"
    )
    plex_token = plex_token or get_env_var("NOTIFIER", name, "PLEX_TOKEN")
    server_library_path = server_library_path or get_env_var(
        "NOTIFIER", name, "PLEX_SERVER_LIBRARY_PATH"
    )

    if not plex_server_url or not plex_token:
        logger.warning("PLEX_SERVER_URL or PLEX_TOKEN not found. Skipping API rescan.")
        return {
            "notebook_id": notebook_id,
            "source": source_dir,
            "status": "partial_success",
            "message": "Plex rescan skipped due to missing credentials.",
        }

    base_url = plex_server_url.rstrip("/")
    refresh_url = f"{base_url}/library/sections/{plex_section_id}/refresh"
    api_path = server_library_path or os.path.realpath(podcast_dir)

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
            "notebook_id": notebook_id,
            "source": source_dir,
            "status": "partial_success",
            "message": f"Plex rescan failed: {e}",
        }

    return {
        "notebook_id": notebook_id,
        "source": source_dir,
        "status": "success",
    }


class PlexNotifier(Notifier):
    """Plex library refresh notifier implementation."""

    def __init__(
        self,
        section_id: Union[int, str],
        server_library_path: Optional[str] = None,
        server_url: Optional[str] = None,
        token: Optional[str] = None,
        name: Optional[str] = None,
    ):
        self.section_id = section_id
        self.server_library_path = server_library_path
        self.server_url = server_url
        self.token = token
        self.name = name

    async def notify(
        self,
        notebook_id: str,
        dist_result: Optional[dict] = None,
        podcast_dir: Optional[str] = None,
    ) -> dict:
        return await sync_to_plex(
            notebook_id=notebook_id,
            plex_section_id=self.section_id,
            podcast_dir=podcast_dir,
            plex_server_url=self.server_url,
            plex_token=self.token,
            server_library_path=self.server_library_path,
            name=self.name,
        )
