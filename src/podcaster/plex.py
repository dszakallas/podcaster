import os
import shutil
import subprocess
import sys
import asyncio
import httpx
from notebooklm import NotebookLMClient
from .utils import get_storage_path, get_notebook_dir_name, load_config, find_notebook_dir

import logging
logger = logging.getLogger(__name__)

async def sync_to_plex(
    notebook_id: str, 
    plex_section_id: str, 
    podcast_dir: str = None,
    plex_server_url: str = None,
    plex_token: str = None,
    server_library_path: str = None,
    grace_period: int = 30
):
    storage_path = get_storage_path()

    # Get Plex config from env or config file
    config = load_config()
    if not podcast_dir:
        podcast_dir = config.get("podcast_dir")
        if not podcast_dir:
            raise ValueError("podcast_dir is missing and not found in config")

    plex_config = config.get("plex", {})

    plex_server_url = plex_server_url or os.environ.get("PLEX_SERVER_URL") or plex_config.get("server_url")
    plex_token = plex_token or os.environ.get("PLEX_TOKEN") or plex_config.get("token")
    server_library_path = server_library_path or os.environ.get("PLEX_SERVER_LIBRARY_PATH") or plex_config.get("server_library_path")

    async with await NotebookLMClient.from_storage(storage_path, timeout=120.0) as client:
        # 1. Fetch notebook details
        notebook = await client.notebooks.get(notebook_id)
        if not notebook:
            raise ValueError(f"Notebook {notebook_id} not found")

        # 2. Construct directory name
        notebook_dir_name = find_notebook_dir(podcast_dir, notebook_id)
        if not notebook_dir_name:
            notebook_dir_name = get_notebook_dir_name(notebook.title, notebook_id, notebook.created_at)

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
                "message": "Plex rescan skipped due to missing credentials."
            }

        # Grace period for Cloud Storage sync
        logger.debug(f"Waiting {grace_period} seconds for Cloud Storage sync...")
        await asyncio.sleep(grace_period)

        # Ensure server URL doesn't have trailing slash
        base_url = plex_server_url.rstrip("/")
        refresh_url = f"{base_url}/library/sections/{plex_section_id}/refresh"

        # Resolve path for Plex API
        # We scan the library root (server_library_path) to ensure new subdirectories are detected.
        # Scanning only the specific notebook directory might fail if the directory is new.
        api_path = server_library_path or os.path.realpath(podcast_dir)

        params = {
            "path": api_path,
            "X-Plex-Token": plex_token,
            "force": "1"
        }
        headers = {
            "X-Plex-Token": plex_token,
            "Accept": "application/json"
        }

        logger.debug(f"Triggering Plex rescan via API: {refresh_url} (path: {api_path})")

        try:
            async with httpx.AsyncClient() as client_http:
                response = await client_http.get(refresh_url, params=params, headers=headers, timeout=10.0)
                response.raise_for_status()
            logger.debug("Plex API rescan triggered successfully.")
        except Exception as e:
            logger.debug(f"Plex API rescan failed: {e}")
            return {
                "notebook_id": notebook_id,
                "source": source_dir,
                "status": "partial_success",
                "message": f"Plex rescan failed: {e}"
            }
        
        logger.debug("Rescan complete.")

    return {
        "notebook_id": notebook_id,
        "source": source_dir,
        "status": "success"
    }
