import asyncio
import os
import sys
import json
import logging
from typing import Optional

from podcaster.audio_gen import core as audio_gen_core
from podcaster import research
from podcaster import plex
from notebooklm import NotebookLMClient
from podcaster.utils import get_storage_path, load_config

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

async def generate_download_and_tag_podcast(
    notebook_id: str,
    task_info: dict,
    cover_image: Optional[str],
    podcast_dir: Optional[str] = None
):
    """Polls, downloads, and tags a specific generation task once complete."""
    logger.debug(f"Polling task {task_info['task_id']}...")
        
    async def task_gen():
        yield task_info
        
    completed_task = None
    async for completed in audio_gen_core.poll_tasks(task_gen()):
        completed_task = completed
        break
        
    if not completed_task:
        raise RuntimeError(f"Task failed to complete: {task_info['task_id']}")
        
    logger.debug(f"Downloading artifact {completed_task['artifact_id']}...")
        
    async def completed_gen():
        yield completed_task
        
    downloaded = None
    async for down in audio_gen_core.download_artifacts(completed_gen(), podcast_dir=podcast_dir):
        downloaded = down
        break
        
    if not downloaded:
        raise RuntimeError(f"Failed to download artifact {completed_task['artifact_id']}")

    logger.debug(f"Tagging artifact {completed_task['artifact_id']}...")

    async def downloaded_gen():
        yield downloaded

    tagged = None
    async for tag in audio_gen_core.tag_artifacts(downloaded_gen(), cover_path=cover_image):
        tagged = tag
        break

    if not tagged:
        raise RuntimeError(f"Failed to tag artifact {completed_task['artifact_id']}")
        
    return tagged

async def run(
    title: str, 
    source_file: str, 
    length: Optional[str] = None, 
    languages: Optional[list[str]] = None, 
    enrich_sources: Optional[bool] = None, 
    generate_cover: Optional[bool] = None, 
    sync_plex: Optional[bool] = None,
    podcast_dir: Optional[str] = None,
    verbose: bool = False
):
    config = load_config()
    wf_config = config.get("workflow", {}).get("deep_dive_single_article", {})
    
    # Resolve defaults
    if enrich_sources is None:
        enrich_sources = wf_config.get("enrich_sources", True)
    if generate_cover is None:
        generate_cover = wf_config.get("generate_cover", True)
    if sync_plex is None:
        sync_plex = wf_config.get("sync_plex", True)
        
    gen_config = config.get("podcast_generation", {})
    if not length:
        length = gen_config.get("length", "long")
    if not languages:
        languages = gen_config.get("languages", ["en"])
        
    logger.info(f"=== Starting Deep Dive Single Article Workflow for '{title}' ===")
    
    # 1. Create notebook (always new)
    notebook_info = await audio_gen_core.init_notebook(title, podcast_dir=podcast_dir)
    notebook_id = notebook_info["notebook_id"]
    logger.info(f"Created notebook: {notebook_id}")
    
    # 2. Upload source
    source_id = await upload_and_wait_source(notebook_id, source_file)
    logger.info(f"Source uploaded and processed: {source_id}")
    
    # Background tasks
    parallel_tasks = []
    
    if enrich_sources:
        async def enrich_task():
            logger.debug("Enriching source...")
            await research.research_from_source(notebook_id, source_id, mode='fast', max_imports=10, verbose=verbose)
            logger.debug("Source enrichment complete.")
        parallel_tasks.append(enrich_task())

    if generate_cover:
        async def cover_task():
            logger.debug("Generating cover...")
            cover_path = await audio_gen_core.generate_cover(notebook_id, podcast_dir=podcast_dir)
            logger.debug(f"Cover generated: {cover_path}")
            return cover_path
        parallel_tasks.append(cover_task())

    results = []
    if parallel_tasks:
        logger.info("Running background tasks (enrichment, cover)...")
        results = await asyncio.gather(*parallel_tasks, return_exceptions=False)
            
    cover_image = None
    if generate_cover:
        # Cover is the last task if both are enabled, or the only task
        cover_image = results[-1]
    
    format_args = {"source_id": source_id}
    
    # 3. Generate podcasts
    logger.info(f"Generating podcasts for languages: {languages} with length: {length}")
    tasks = []
    async for task in audio_gen_core.generate_tasks(
        notebook_id,
        "main-article-with-author",
        languages,
        length,
        json.dumps(format_args),
        dry_run=False
    ):
        tasks.append(task)
        
    logger.info(f"Generation tasks started: {[t['task_id'] for t in tasks]}")
    
    # 4. Poll, download and tag
    logger.info("Polling, downloading and tagging in parallel...")
    processing_coros = [
        generate_download_and_tag_podcast(notebook_id, task, cover_image, podcast_dir=podcast_dir)
        for task in tasks
    ]
    processed_files = await asyncio.gather(*processing_coros, return_exceptions=False)
            
    logger.info(f"Processed podcasts: {[pf['path'] for pf in processed_files]}")
    
    # 5. Sync to Plex
    if sync_plex:
        plex_config = config.get("plex", {})
        plex_section_id = plex_config.get("section_id") 
        if not plex_section_id:
            logger.warning("plex.section_id is missing from config, skipping Plex sync")
        else:
            logger.info(f"Syncing to Plex (section {plex_section_id})...")
            await plex.sync_to_plex(
                notebook_id=notebook_id,
                plex_section_id=plex_section_id,
                podcast_dir=podcast_dir,
                verbose=verbose
            )
    
    logger.info("=== Workflow Complete ===")
    return {"notebook_id": notebook_id, "files": [pf['path'] for pf in processed_files]}
