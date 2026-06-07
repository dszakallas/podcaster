import os
import yaml
import sys
import asyncio
import time
import importlib
import math
import tempfile
import shutil
from pathlib import Path
from typing import AsyncGenerator, Optional
from notebooklm import NotebookLMClient
from notebooklm.rpc import AudioLength
from .params import AudioGenParams
from .. import tagging
from ..utils import load_config, get_storage_path, sanitize, get_notebook_dir_name, find_notebook_dir, get_or_create_notebook_dir, DEFAULT_PODCAST_DIR

import logging
logger = logging.getLogger(__name__)

LENGTH_MAP = {
    "short": AudioLength.SHORT,
    "default": AudioLength.DEFAULT,
    "long": AudioLength.LONG
}

DURATION_MAP = {
    "short": "10 minutes",
    "default": "20 minutes",
    "long": "30 minutes"
}

LANGUAGES_SUPPORTING_LENGTH = {"en"}

def load_plugin(type_name: str):
    # Convert hyphenated name to underscore for python module
    module_name = type_name.replace("-", "_")
    try:
        module = importlib.import_module(f"podcaster.audio_gen.tasks.gen_podcast.{module_name}.{module_name}")
        return module
    except ImportError as e:
        raise ImportError(f"Task plugin '{type_name}' not found in podcaster.audio_gen.tasks.gen_podcast. Detail: {e}")

async def generate_tasks(
    notebook_id: str, 
    type_name: str, 
    languages: list[str], 
    length_str: Optional[str], 
    format_args_json: str, 
    dry_run: bool = False
) -> AsyncGenerator[dict, None]:
    config = load_config()
    gen_defaults = config.get("podcast_generation", {})
    
    if not languages:
        languages = gen_defaults.get("languages", ["en"])
    
    if not length_str:
        length_str = gen_defaults.get("length", "long")
    
    storage_path = get_storage_path()

    plugin = load_plugin(type_name)
    inputs = plugin.Inputs.model_validate_json(format_args_json or "{}")

    async with await NotebookLMClient.from_storage(storage_path, timeout=120.0) as client:
        critical_path = Path(__file__).parent / "data" / "critical.txt"
        critical_content = critical_path.read_text() if critical_path.exists() else ""
        params = AudioGenParams(notebook_id=notebook_id, length=length_str)
        plugin_prompt = await plugin.get_prompt(client, inputs, params)
        instructions = f"{critical_content}\n\n{plugin_prompt}"

        if dry_run:
            return

        ETA_MAP = {"short": 8.0, "default": 12.0, "long": 16.0}
        eta = ETA_MAP.get(length_str, 10.0)

        for lang_code in languages:
            audio_length = LENGTH_MAP[length_str]
            if lang_code not in LANGUAGES_SUPPORTING_LENGTH and length_str == "long":
                audio_length = AudioLength.DEFAULT

            try:
                status = await client.artifacts.generate_audio(
                    notebook_id,
                    language=lang_code,
                    instructions=instructions,
                    audio_length=audio_length
                )
                yield {
                    "notebook_id": notebook_id, 
                    "task_id": status.task_id, 
                    "eta": eta,
                    "metadata": {
                        "generate-podcast": {
                            "language": lang_code,
                            "type": type_name,
                            "length": length_str,
                            "format_args": inputs.model_dump()
                        }
                    }
                }
            except Exception as e:
                logger.debug(f"[{lang_code}] Generation failed: {e}")

async def _poll_single_task(
    client: NotebookLMClient, 
    notebook_id: str, 
    task_id: str, 
    lang_code: str, 
    eta: float
) -> Optional[dict]:
    target_time = eta * 60
    start_time = time.monotonic()
    log2_30 = math.log2(30.0)
    
    k_denom = abs(300.0 - target_time)
    k = max(1.0, k_denom / log2_30)
    
    first_poll = True
    while True:
        try:
            artifacts = await client.artifacts.list(notebook_id)
            artifact = next((a for a in artifacts if a.id == task_id), None)
            
            if artifact:
                status_val = artifact.status
                status_name = artifact.status.name.lower() if hasattr(artifact.status, 'name') else str(artifact.status).lower()
                
                logger.debug(f"[{lang_code}] Task {task_id} status: {status_name} (val: {status_val})", file=sys.stderr)
                
                if status_val == 3 or status_name == "completed":
                    return {
                        "notebook_id": notebook_id, 
                        "artifact_id": task_id,
                        "title": artifact.title,
                        "created_at": artifact.created_at.isoformat() if hasattr(artifact, 'created_at') and artifact.created_at else None
                    }
                elif status_val == 4 or status_name in ("failed", "error", "unknown"):
                    return None
            else:
                logger.debug(f"[{lang_code}] Task {task_id} not found in artifact list. Terminating.")
                return None
        except Exception as e:
            logger.debug(f"Polling error: {e}")

        if first_poll:
            logger.debug(f"[{lang_code}] Waiting 5 minutes before next poll...")
            await asyncio.sleep(300)
            first_poll = False
            continue

        elapsed = time.monotonic() - start_time
        interval = max(10.0, 10.0 * math.pow(2.0, abs(elapsed - target_time) / k))
        await asyncio.sleep(interval)

async def poll_tasks(
    tasks: AsyncGenerator[dict, None]
) -> AsyncGenerator[dict, None]:
    storage_path = get_storage_path()

    async with await NotebookLMClient.from_storage(storage_path, timeout=120.0) as client:
        pending = set()
        
        async def wrap_poll(task):
            lang_code = task.get("metadata", {}).get("generate-podcast", {}).get("language", "unknown")
            task_id = task["task_id"]
            res = await _poll_single_task(
                client, 
                task["notebook_id"], 
                task_id, 
                lang_code,
                task.get("eta", 10.0)
            )
            if res:
                # Merge original metadata and add poller metadata
                metadata = task.get("metadata", {}).copy()
                metadata["poll-artifact-task"] = {
                    "task_id": task_id,
                    "status": "completed",
                    "polled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                }
                res["metadata"] = metadata
            return res

        async for task in tasks:
            pending.add(asyncio.create_task(wrap_poll(task)))

        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for fut in done:
                result = await fut
                if result:
                    yield result

async def download_artifacts(
    artifacts: AsyncGenerator[dict, None], 
    podcast_dir: Optional[str] = None
) -> AsyncGenerator[dict, None]:
    storage_path = get_storage_path()

    config = load_config()
    if not podcast_dir:
        podcast_dir = config.get("podcast_dir", DEFAULT_PODCAST_DIR)

    os.makedirs(podcast_dir, exist_ok=True)
    
    async with await NotebookLMClient.from_storage(storage_path, timeout=120.0) as client:
        async for art in artifacts:
            notebook_id = art["notebook_id"]
            artifact_id = art["artifact_id"]
            title = art.get("title", artifact_id)
            
            # Fetch notebook for directory name
            notebook = await client.notebooks.get(notebook_id)
            album = notebook.title if notebook else "NotebookLM Podcast"

            # Organize by notebook directory
            notebook_dir = get_or_create_notebook_dir(podcast_dir, notebook_id, album, notebook.created_at if notebook else None)

            # Filename based on title suffixed with id
            safe_title = sanitize(title)
            filename = f"{safe_title} [{artifact_id}].m4a"
            out_path = os.path.join(notebook_dir, filename)
            
            try:
                logger.debug(f"Downloading {artifact_id} to {out_path}...")
                await client.artifacts.download_audio(notebook_id, out_path, artifact_id=artifact_id)
                
                yield {**art, "path": out_path, "filename": filename}
            except Exception as e:
                logger.debug(f"Download failed for {artifact_id}: {e}")

async def tag_artifacts(
    artifacts: AsyncGenerator[dict, None], 
    cover_path: Optional[str] = None,
    track_offset: int = 0
) -> AsyncGenerator[dict, None]:
    storage_path = get_storage_path()

    config = load_config()
    tags_config = config.get("podcast_tags", {})
    default_album_artist = tags_config.get("album_artist", "Dávid Szakállas")
    default_artists = tags_config.get("artists", ["Dávid Szakállas"])

    async with await NotebookLMClient.from_storage(storage_path, timeout=120.0) as client:
        # Counter for generated track numbers
        auto_track_count = 0

        async for art in artifacts:
            notebook_id = art["notebook_id"]
            artifact_id = art["artifact_id"]
            title = art.get("title", artifact_id)
            out_path = art["path"]
            metadata = art.get("metadata", {})
            gen_podcast_meta = metadata.get("generate-podcast", {})
            language = gen_podcast_meta.get("language")
            created_at = art.get("created_at")
            
            # Fetch notebook for album title
            notebook = await client.notebooks.get(notebook_id)
            album = notebook.title if notebook else "NotebookLM Podcast"
            source_url = f"https://notebooklm.google.com/notebook/{notebook_id}"

            # Determine track number
            explicit_track = art.get("track")
            if explicit_track is not None:
                track_number = explicit_track
            else:
                auto_track_count += 1
                track_number = track_offset + auto_track_count
            
            try:
                logger.debug(f"Tagging {out_path} (Track: {track_number})...")

                # Tagging the file in-place
                tagging.tag_file(
                    audio_file=out_path,
                    cover=cover_path,
                    title=title,
                    album=album,
                    track=track_number,
                    date=created_at[:10] if created_at else None,
                    artists=default_artists,
                    album_artist=default_album_artist,
                    source=source_url,
                    in_place=True,
                    out=None,
                    language=language
                )
                
                # Update metadata to show it was tagged
                metadata["tag-podcast"] = {
                    "tagged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "track": track_number,
                    "cover": cover_path
                }
                
                yield {**art, "metadata": metadata, "track": track_number}
            except Exception as e:
                logger.debug(f"Tagging failed for {artifact_id}: {e}")
                yield art

async def generate_cover(notebook_id: str, podcast_dir: Optional[str] = None) -> str:
    from google import genai
    from google.genai import types
    import time
    import io
    from PIL import Image

    storage_path = get_storage_path()
    
    config = load_config()
    if not podcast_dir:
        podcast_dir = config.get("podcast_dir", DEFAULT_PODCAST_DIR)

    async with await NotebookLMClient.from_storage(storage_path, timeout=120.0) as client:
        notebook = await client.notebooks.get(notebook_id)
        if not notebook:
            raise ValueError(f"Notebook {notebook_id} not found")
        
        summary = await client.notebooks.get_summary(notebook_id)
        
        chat_prompt = f"Based on this notebook summary, create a prompt for generating a podcast album cover. The title of the podcast is '{notebook.title}'. Please ensure the prompt instructs the generator to include the text '{notebook.title}' clearly as the title on the cover. Summary: {summary}. Generate ONLY one prompt, nothing else."
        image_gen_result = await client.chat.ask(notebook_id, chat_prompt)
        image_gen_prompt = image_gen_result.answer
        
        logger.debug(f"Generated prompt for image: {image_gen_prompt}")

        genai_client = genai.Client()
        
        # Using Batch API for 50% lower cost. 
        # Note: Turnaround time can be longer, but we will poll until completion.
        logger.debug("Submitting batch job for image generation...")
            
        batch_job = genai_client.batches.create(
            model='gemini-3.1-flash-image',
            src=[
                types.InlinedRequest(
                    model='gemini-3.1-flash-image',
                    contents=[
                        types.Content(
                            parts=[types.Part(text=image_gen_prompt)],
                            role="user"
                        )
                    ],
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"],
                        image_config=types.ImageConfig(
                            aspect_ratio="1:1"
                        )
                    )
                )
            ]
        )
        
        job_id = batch_job.name
        logger.debug(f"Batch job created: {job_id}. Polling for completion...")
            
        while True:
            job = genai_client.batches.get(name=job_id)
            state = str(job.state)
            logger.debug(f"Job state: {state}")
                
            if "SUCCEEDED" in state:
                break
            elif "FAILED" in state or "CANCELLED" in state:
                error_msg = f"Batch job {state}. Error: {job.error}" if job.error else f"Batch job {state}."
                raise RuntimeError(error_msg)
            
            await asyncio.sleep(30)
            
        # Retrieve results from inlined_responses
        if not job.dest or not job.dest.inlined_responses:
            raise RuntimeError("Batch job succeeded but no results found in inlined_responses.")
            
        inlined_res = job.dest.inlined_responses[0]
        if inlined_res.error:
            raise RuntimeError(f"Batch job inlined response error: {inlined_res.error}")
            
        response = inlined_res.response
        if not response:
             raise RuntimeError("Batch job inlined response is empty (None).")
             
        if not response.candidates or not response.candidates[0].content.parts:
            raise RuntimeError("Batch job response has no candidates or parts.")
            
        image_part = response.candidates[0].content.parts[0]
        if not image_part.inline_data:
            raise RuntimeError("Batch job response part does not contain inline_data (image).")
            
        image_bytes = image_part.inline_data.data
        image = Image.open(io.BytesIO(image_bytes))
        
        notebook_dir = get_or_create_notebook_dir(podcast_dir, notebook_id, notebook.title, notebook.created_at)
        
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"album_cover_{timestamp}.png"
        out_path = os.path.join(notebook_dir, filename)
        image.save(out_path)
        
        return out_path

async def init_notebook(title: str, podcast_dir: Optional[str] = None) -> dict:
    """Create a new notebook and its local directory."""
    storage_path = get_storage_path()
    
    config = load_config()
    if not podcast_dir:
        podcast_dir = config.get("podcast_dir", DEFAULT_PODCAST_DIR)
        
    async with await NotebookLMClient.from_storage(storage_path, timeout=120.0) as client:
        logger.debug(f"Creating notebook: {title}")
        notebook = await client.notebooks.create(title)
        
        # Re-fetch to get created_at if it was None
        if not notebook.created_at:
            logger.debug(f"Fetching full details for notebook {notebook.id}...")
            notebook = await client.notebooks.get(notebook.id)
            
        logger.debug(f"Initializing directory for notebook {notebook.id} in {podcast_dir}")
        notebook_dir = get_or_create_notebook_dir(podcast_dir, notebook.id, notebook.title, notebook.created_at)
        
        return {
            "notebook_id": notebook.id,
            "created_at": notebook.created_at.isoformat() if notebook.created_at else None,
            "local_dir": os.path.basename(notebook_dir)
        }
