import asyncio
import os
import sys
import json
from podcaster.audio_gen import core as audio_gen_core
from podcaster import research
from podcaster import plex
from notebooklm import NotebookLMClient
from podcaster.utils import get_storage_path, load_config

import logging
logger = logging.getLogger(__name__)

async def upload_and_wait_source(notebook_id: str, source_file: str) -> str:
    """Uploads a source file via the notebooklm client and waits for processing."""
    logger.debug(f"Uploading and processing {source_file}...")
        
    storage_path = get_storage_path()
    async with await NotebookLMClient.from_storage(storage_path, timeout=120.0) as client:
        source = await client.sources.add_file(
            notebook_id, 
            source_file, 
            wait=True, 
            wait_timeout=600.0  # Allow up to 10 minutes for processing
        )
        logger.debug(f"Source ready: {source.id}")
        return source.id

async def generate_and_download_podcast(
    notebook_id: str,
    task_info: dict,
    cover_image: str
):
    """Polls a specific generation task and downloads it once complete."""
    logger.debug(f"Polling task {task_info['task_id']}...")
        
    async def task_gen():
        yield task_info
        
    completed_task = None
    async for completed in audio_gen_core.poll_tasks(task_gen()):
        completed_task = completed
        break # We only yield one task, so we break after the first completion
        
    if not completed_task:
        raise RuntimeError(f"Task failed to complete: {task_info['task_id']}")
        
    logger.debug(f"Downloading artifact {completed_task['artifact_id']}...")
        
    async def completed_gen():
        yield completed_task
        
    downloaded = None
    async for down in audio_gen_core.download_artifacts(completed_gen(), cover_path=cover_image):
        downloaded = down
        break
        
    if not downloaded:
        raise RuntimeError(f"Failed to download artifact {completed_task['artifact_id']}")
        
    return downloaded

async def run_workflow(title: str, source_file: str, length: str, languages: list[str]):
    config = load_config()
    if not length:
        length = config.get("generate", {}).get("length")
        if not length:
            raise ValueError("Target length not specified and not found in config")
    if not languages:
        languages = config.get("generate", {}).get("languages")
        if not languages:
            raise ValueError("Target languages not specified and not found in config")
        
    logger.info(f"=== Starting Podcast Workflow for '{title}' ===")
    
    # 1. Create notebook
    notebook_info = await audio_gen_core.init_notebook(title)
    notebook_id = notebook_info["notebook_id"]
    logger.info(f"Created notebook: {notebook_id}")
    
    # 2. Upload source
    source_id = await upload_and_wait_source(notebook_id, source_file)
    logger.info(f"Source uploaded and processed: {source_id}")
    
    # Branch out parallel tasks
    async def enrich_task():
        logger.debug("Enriching source...")
        await research.research_from_source(notebook_id, source_id, mode='fast', max_imports=10)
        logger.debug("Source enrichment complete.")

    async def cover_task():
        logger.debug("Generating cover...")
        cover_path = await audio_gen_core.generate_cover(notebook_id)
        logger.debug(f"Cover generated: {cover_path}")
        return cover_path


    # 3-5. Run enrich, cover, and format args in parallel
    logger.info("Running enrichment and cover generation in parallel...")
    results = await asyncio.gather(
        enrich_task(),
        cover_task(),
        return_exceptions=True
    )
    
    for res in results:
        if isinstance(res, Exception):
            logger.info(f"Error during parallel step: {res}")
            raise res
            
    cover_image = results[1]
    format_args = {"source_id": source_id}
    
    # 6. Generate podcasts
    logger.info(f"Generating podcasts for languages: {languages} with length: {length}")
    tasks = []
    async for task in audio_gen_core.generate_tasks(
        notebook_id,
        "main_article_with_author",
        languages,
        length,
        json.dumps(format_args),
        dry_run=False
    ):
        tasks.append(task)
        
    logger.info(f"Generation tasks started: {[t['task_id'] for t in tasks]}")
    
    # 7 & 8. Poll and download in parallel
    logger.info("Polling and downloading in parallel...")
    download_coros = [
        generate_and_download_podcast(notebook_id, task, cover_image)
        for task in tasks
    ]
    downloaded_files = await asyncio.gather(*download_coros, return_exceptions=True)
    
    for df in downloaded_files:
        if isinstance(df, Exception):
            logger.info(f"Error during download step: {df}")
            raise df
            
    logger.info(f"Downloaded podcasts: {[df['path'] for df in downloaded_files]}")
    
    # 9. Sync to Plex
    plex_section_id = config.get("plex", {}).get("section_id") 
    if not plex_section_id:
        raise ValueError("plex.section_id is missing from config")
        
    logger.info(f"Syncing to Plex (section {plex_section_id})...")
    sync_result = await plex.sync_to_plex(
        notebook_id=notebook_id,
        plex_section_id=plex_section_id
    )
    
    logger.info("=== Podcast Workflow Complete ===")
    return sync_result
