import asyncio
import importlib
import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import AsyncGenerator, Optional

from notebooklm import NotebookLMClient
from notebooklm.exceptions import NotebookNotFoundError
from notebooklm.rpc import AudioLength

from ..utils import (
    get_or_create_notebook_dir,
    get_storage_path,
    load_config,
    sanitize,
)
from .params import AudioGenParams

logger = logging.getLogger(__name__)

LENGTH_MAP = {
    "short": AudioLength.SHORT,
    "default": AudioLength.DEFAULT,
    "long": AudioLength.LONG,
}

DURATION_MAP = {"short": "10 minutes", "default": "20 minutes", "long": "30 minutes"}

LANGUAGES_SUPPORTING_LENGTH = {"en"}


def load_plugin(type_name: str):
    # Convert hyphenated name to underscore for python module
    module_name = type_name.replace("-", "_")
    try:
        module = importlib.import_module(
            f"podcaster.audio_gen.tasks.gen_podcast.{module_name}.{module_name}"
        )
        return module
    except ImportError as e:
        raise ImportError(
            f"Task plugin '{type_name}' not found in podcaster.audio_gen.tasks.gen_podcast. Detail: {e}"
        )


async def generate_tasks(
    notebook_id: str,
    type_name: str,
    languages: list[str],
    length_str: Optional[str],
    format_args_json: str,
    dry_run: bool = False,
) -> AsyncGenerator[dict, None]:
    config = load_config()
    gen_defaults = config.podcast_generation

    if not languages:
        languages = gen_defaults.languages

    if not length_str:
        length_str = gen_defaults.length

    storage_path = get_storage_path()

    plugin = load_plugin(type_name)
    inputs = plugin.Inputs.model_validate_json(format_args_json or "{}")

    async with await NotebookLMClient.from_storage(
        storage_path, timeout=120.0
    ) as client:
        critical_path = Path(__file__).parent / "data" / "critical.md"
        critical_content = critical_path.read_text()
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
                    audio_length=audio_length,
                )
                if status.status == "failed" or not status.task_id:
                    raise RuntimeError(status.error or "No task ID returned")
                yield {
                    "notebook_id": notebook_id,
                    "task_id": status.task_id,
                    "eta": eta,
                    "metadata": {
                        "generate-podcast": {
                            "language": lang_code,
                            "type": type_name,
                            "length": length_str,
                            "format_args": inputs.model_dump(),
                        }
                    },
                }
            except Exception as e:
                logger.error(f"[{lang_code}] Generation failed: {e}")
                raise


async def _poll_single_task(
    client: NotebookLMClient, notebook_id: str, task_id: str, lang_code: str, eta: float
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
                status_name = (
                    artifact.status.name.lower()
                    if hasattr(artifact.status, "name")
                    else str(artifact.status).lower()
                )

                logger.debug(
                    f"[{lang_code}] Task {task_id} status: {status_name} (val: {status_val})",
                    file=sys.stderr,
                )

                if status_val == 3 or status_name == "completed":
                    return {
                        "notebook_id": notebook_id,
                        "artifact_id": task_id,
                        "title": artifact.title,
                        "created_at": (
                            artifact.created_at.isoformat()
                            if hasattr(artifact, "created_at") and artifact.created_at
                            else None
                        ),
                    }
                elif status_val == 4 or status_name in ("failed", "error", "unknown"):
                    return None
            else:
                logger.debug(
                    f"[{lang_code}] Task {task_id} not found in artifact list. Terminating."
                )
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


async def poll_tasks(tasks: AsyncGenerator[dict, None]) -> AsyncGenerator[dict, None]:
    storage_path = get_storage_path()

    async with await NotebookLMClient.from_storage(
        storage_path, timeout=120.0
    ) as client:
        pending = set()

        async def wrap_poll(task):
            lang_code = (
                task.get("metadata", {})
                .get("generate-podcast", {})
                .get("language", "unknown")
            )
            task_id = task["task_id"]
            res = await _poll_single_task(
                client, task["notebook_id"], task_id, lang_code, task.get("eta", 10.0)
            )
            if res:
                # Merge original metadata and add poller metadata
                metadata = task.get("metadata", {}).copy()
                metadata["poll-artifact-task"] = {
                    "task_id": task_id,
                    "status": "completed",
                    "polled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                res["metadata"] = metadata
            return res

        async for task in tasks:
            pending.add(asyncio.create_task(wrap_poll(task)))

        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for fut in done:
                result = await fut
                if result:
                    yield result


async def download_artifacts(
    artifacts: AsyncGenerator[dict, None], podcast_dir: Optional[str] = None
) -> AsyncGenerator[dict, None]:
    storage_path = get_storage_path()

    config = load_config()
    if not podcast_dir:
        podcast_dir = config.podcast_dir

    os.makedirs(podcast_dir, exist_ok=True)

    async with await NotebookLMClient.from_storage(
        storage_path, timeout=120.0
    ) as client:
        async for art in artifacts:
            notebook_id = art["notebook_id"]
            artifact_id = art["artifact_id"]
            title = art.get("title", artifact_id)

            # Fetch notebook for directory name
            try:
                notebook = await client.notebooks.get(notebook_id)
                album = notebook.title if notebook else "NotebookLM Podcast"
            except NotebookNotFoundError:
                notebook = None
                album = "NotebookLM Podcast"

            # Organize by notebook directory
            notebook_dir = get_or_create_notebook_dir(
                podcast_dir,
                notebook_id,
                album,
                notebook.created_at if notebook else None,
            )

            # Filename based on title suffixed with id
            safe_title = sanitize(title)
            filename = f"{safe_title} [{artifact_id}].m4a"
            out_path = os.path.join(notebook_dir, filename)

            try:
                logger.debug(f"Downloading {artifact_id} to {out_path}...")
                await client.artifacts.download_audio(
                    notebook_id, out_path, artifact_id=artifact_id
                )

                yield {
                    **art,
                    "path": out_path,
                    "filename": filename,
                    "album": album,
                    "created_at": (
                        notebook.created_at.isoformat()
                        if notebook and notebook.created_at
                        else art.get("created_at")
                    ),
                }
            except Exception as e:
                logger.debug(f"Download failed for {artifact_id}: {e}")
