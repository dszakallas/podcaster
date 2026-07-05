import logging
import os
from typing import Optional

import httpx

from .utils import (
    find_notebook_dir,
    load_config,
    rclone_copy_dir,
    rsync_dir,
    setup_logging,
)

logger = logging.getLogger(__name__)


async def sync_podcast(
    notebook_id: str,
    destination: str,
    method: str = "rsync",
    podcast_dir: Optional[str] = None,
    verbose: bool = False,
) -> dict:
    if verbose:
        setup_logging(verbose)

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

    dst_path = os.path.join(destination, notebook_dir_name)

    if method == "rsync":
        logger.info(f"Rsyncing {source_dir} to {dst_path}...")
        rsync_dir(source_dir, dst_path)
    elif method == "rclone":
        logger.info(f"Rclone copying {source_dir} to {dst_path}...")
        rclone_copy_dir(source_dir, dst_path)
    else:
        raise ValueError(f"Unknown sync method: {method}")

    return {
        "notebook_id": notebook_id,
        "source": source_dir,
        "destination": dst_path,
        "method": method,
        "status": "success",
    }


async def sync_to_plex(
    notebook_id: str,
    plex_section_id: str,
    podcast_dir: Optional[str] = None,
    plex_server_url: Optional[str] = None,
    plex_token: Optional[str] = None,
    server_library_path: Optional[str] = None,
    rsync_destination: Optional[str] = None,
    sync_method: str = "rsync",
    verbose: bool = False,
):
    if verbose:
        setup_logging(verbose)

    # Get Plex config from env or config file
    config = load_config()
    if not podcast_dir:
        podcast_dir = config.podcast_dir

    plex_config = config.plex

    plex_server_url = (
        plex_server_url or os.environ.get("PLEX_SERVER_URL") or plex_config.server_url
    )
    plex_token = plex_token or os.environ.get("PLEX_TOKEN") or plex_config.token
    server_library_path = (
        server_library_path
        or os.environ.get("PLEX_SERVER_LIBRARY_PATH")
        or plex_config.server_library_path
    )

    # If rsync destination is provided, do rsync/rclone first
    if rsync_destination:
        await sync_podcast(
            notebook_id,
            rsync_destination,
            method=sync_method,
            podcast_dir=podcast_dir,
            verbose=verbose,
        )

    # 1. Construct directory name using find_notebook_dir
    notebook_dir_name = find_notebook_dir(podcast_dir, notebook_id)
    if not notebook_dir_name:
        raise FileNotFoundError(
            f"Notebook directory not found for notebook ID: {notebook_id}"
        )

    source_dir = os.path.join(podcast_dir, notebook_dir_name)

    if not os.path.exists(source_dir):
        raise FileNotFoundError(f"Source directory {source_dir} not found.")

    # Trigger Plex rescan via REST API
    if not plex_server_url or not plex_token:
        logger.debug("PLEX_SERVER_URL or PLEX_TOKEN not found. Skipping API rescan.")
        return {
            "notebook_id": notebook_id,
            "source": source_dir,
            "status": "partial_success",
            "message": "Plex rescan skipped due to missing credentials.",
        }

    # Ensure server URL doesn't have trailing slash
    base_url = plex_server_url.rstrip("/")
    refresh_url = f"{base_url}/library/sections/{plex_section_id}/refresh"

    # Resolve path for Plex API
    # We scan the library root (server_library_path) to ensure new subdirectories are detected.
    # Scanning only the specific notebook directory might fail if the directory is new.
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

    logger.debug("Rescan complete.")

    return {"notebook_id": notebook_id, "source": source_dir, "status": "success"}
