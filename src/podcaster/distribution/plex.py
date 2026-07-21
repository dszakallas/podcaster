import logging
import os
from typing import Optional, Union

import httpx

from ..config import AppConfig, PlexRsyncConfig
from ..utils import find_notebook_dir, load_config, setup_logging
from .base import Distribution
from .rsync import sync_podcast

logger = logging.getLogger(__name__)


async def sync_to_plex(
    notebook_id: str,
    plex_section_id: Union[int, str],
    podcast_dir: Optional[str] = None,
    plex_server_url: Optional[str] = None,
    plex_token: Optional[str] = None,
    server_library_path: Optional[str] = None,
    rsync_destination: Optional[str] = None,
    sync_method: str = "rsync",
    verbose: bool = False,
    flags: Optional[list[str]] = None,
) -> dict:
    if verbose:
        setup_logging(verbose)

    config = load_config()
    if not podcast_dir:
        podcast_dir = config.podcast_dir

    if rsync_destination:
        await sync_podcast(
            notebook_id,
            rsync_destination,
            method=sync_method,
            podcast_dir=podcast_dir,
            verbose=verbose,
            flags=flags,
        )

    notebook_dir_name = find_notebook_dir(podcast_dir, notebook_id)
    if not notebook_dir_name:
        raise FileNotFoundError(
            f"Notebook directory not found for notebook ID: {notebook_id}"
        )

    source_dir = os.path.join(podcast_dir, notebook_dir_name)

    if not os.path.exists(source_dir):
        raise FileNotFoundError(f"Source directory {source_dir} not found.")

    plex_server_url = plex_server_url or os.environ.get("PLEX_SERVER_URL")
    plex_token = plex_token or os.environ.get("PLEX_TOKEN")
    server_library_path = server_library_path or os.environ.get(
        "PLEX_SERVER_LIBRARY_PATH"
    )

    if not plex_server_url or not plex_token:
        logger.debug("PLEX_SERVER_URL or PLEX_TOKEN not found. Skipping API rescan.")
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


class PlexDistribution(Distribution):
    """Plex distribution mechanism implementation."""

    def __init__(
        self,
        section_id: Union[int, str],
        server_library_path: Optional[str] = None,
        server_url: Optional[str] = None,
        token: Optional[str] = None,
        rsync_config: Optional[PlexRsyncConfig] = None,
        config: Optional[AppConfig] = None,
    ):
        self.section_id = section_id
        self.server_library_path = server_library_path
        self.server_url = server_url
        self.token = token
        self.rsync_config = rsync_config
        self.config = config

    async def distribute(
        self,
        notebook_id: str,
        podcast_dir: Optional[str] = None,
        verbose: bool = False,
    ) -> dict:
        rsync_dest = None
        rsync_method = "rsync"
        flags = None

        if self.rsync_config and self.rsync_config.enable:
            spec = self.rsync_config.spec
            if spec is not None:
                spec_ref = spec.ref
                if spec_ref and self.config and spec_ref in self.config.distributions:
                    ref_dist = self.config.distributions[spec_ref]
                    if ref_dist.rsync:
                        rsync_dest = ref_dist.rsync.destination
                        rsync_method = ref_dist.rsync.method or "rsync"
                        flags = ref_dist.rsync.flags
                elif spec.destination:
                    rsync_dest = spec.destination
                    rsync_method = spec.method or "rsync"
                    flags = spec.flags

        return await sync_to_plex(
            notebook_id=notebook_id,
            plex_section_id=self.section_id,
            podcast_dir=podcast_dir,
            plex_server_url=self.server_url,
            plex_token=self.token,
            server_library_path=self.server_library_path,
            rsync_destination=rsync_dest,
            sync_method=rsync_method,
            verbose=verbose,
            flags=flags,
        )
