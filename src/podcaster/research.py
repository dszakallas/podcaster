import asyncio
import os
import random
import sys
from typing import Optional
from notebooklm import NotebookLMClient
from .utils import get_storage_path, setup_logging

import logging
logger = logging.getLogger(__name__)

async def research_from_source(
    notebook_id: str, 
    source_id: str, 
    mode: str = "fast", 
    max_imports: Optional[int] = None,
    verbose: bool = False
) -> dict:
    if verbose:
        setup_logging(verbose)
        
    storage_path = get_storage_path()
    async with await NotebookLMClient.from_storage(storage_path, timeout=120.0) as client:
        # 1. Fetch source guide for keywords
        logger.debug(f"Fetching guide for source {source_id}...")
        guide = await client.sources.get_guide(notebook_id, source_id)
        keywords = guide.get("keywords", [])
        if not keywords:
            logger.debug(f"No keywords found in guide for source {source_id}.")
            topic = "general" # Fallback topic
        else:
            topic = ", ".join(keywords)

        # 2. Get one-sentence summary with dates
        logger.debug(f"Generating summary for source {source_id}...")
        summary_q = "Summarize this source in exactly one sentence. The summary MUST contain dates for the most important events to better ground the research."
        summary_res = await client.chat.ask(notebook_id, summary_q, source_ids=[source_id])
        summary = summary_res.answer

        # 3. Assemble research prompt
        prompt = f"Topic: {topic}. Context: {summary}"
        logger.debug(f"Starting research with prompt: {prompt} (mode: {mode})")

        # 4. Start research
        job = await client.research.start(notebook_id, prompt, mode=mode)
        if not job:
            raise RuntimeError("Failed to start research job.")
        
        task_id = job.get("task_id")
        
        # 5. Poll for completion
        while True:
            res = await client.research.poll(notebook_id)
            # Based on empirical observation, status is 'completed'
            status = res.get("status")
            logger.debug(f"Research status: {status}")
                
            if status == "completed":
                found_sources = res.get("sources", [])
                break
            elif status == "failed":
                raise RuntimeError(f"Research job failed: {res}")
            
            await asyncio.sleep(5)

        logger.debug(f"Found {len(found_sources)} sources.")

        # 6. Select sources to import
        sources_to_import = found_sources
        if max_imports is not None and len(found_sources) > max_imports:
            sources_to_import = random.sample(found_sources, max_imports)
            logger.debug(f"Randomly selected {len(sources_to_import)} sources to import.")

        # 7. Import sources
        if sources_to_import:
            logger.debug(f"Importing {len(sources_to_import)} sources...")
            imported = await client.research.import_sources(notebook_id, task_id, sources_to_import)
            return {
                "notebook_id": notebook_id,
                "task_id": task_id,
                "topic": topic,
                "summary": summary,
                "found_count": len(found_sources),
                "imported_count": len(imported),
                "imported": imported
            }
        else:
            return {
                "notebook_id": notebook_id,
                "task_id": task_id,
                "topic": topic,
                "summary": summary,
                "found_count": len(found_sources),
                "imported_count": 0,
                "imported": []
            }
