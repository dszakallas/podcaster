import asyncio
import importlib
import logging
import math
import os
import time
from pathlib import Path
from typing import Any, AsyncGenerator, AsyncIterable, Optional, Union

from notebooklm.rpc.types import AudioLength

from ..config import NotebookLMConfig, PodcastGenerationConfig
from ..models import PodcastGenArtifact, PodcastGenTask, TaskStatus
from ..utils.duration import parse_duration_minutes, resolve_duration
from ..utils.files import sanitize
from ..utils.notebooklm import RetryingNotebookLMClient, get_notebooklm_client
from ..utils.retry import is_transient_network_exception
from .params import AudioGenParams

logger = logging.getLogger(__name__)


LANGUAGES_SUPPORTING_LENGTH = {"en"}
DEFAULT_AUDIO_MINUTES = 20
MIN_POLL_INTERVAL = 10.0
MAX_POLL_INTERVAL = 120.0
INITIAL_POLL_INTERVAL = 30.0
POST_ETA_HALF_LIFE = 600.0
MAX_POLL_TIMEOUT_SECONDS = 1800.0


def duration_to_audio_length(duration_str: str) -> AudioLength:
    """Map a duration string to the nearest NotebookLM AudioLength bucket.

    Thresholds (in minutes):
      <= 12  -> SHORT
      <= 25  -> DEFAULT
      >  25  -> LONG
    """
    minutes = parse_duration_minutes(duration_str)
    if minutes is None:
        return AudioLength.DEFAULT
    if minutes <= 12:
        return AudioLength.SHORT
    if minutes <= 24:
        return AudioLength.DEFAULT
    return AudioLength.LONG


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


async def create_podcast_audio_jobs(
    notebook_id: str,
    type_name: str,
    languages: list[str],
    length_str: Optional[str],
    format_args: dict[str, Any],
    generator_config: PodcastGenerationConfig,
    notebooklm_config: NotebookLMConfig,
    dry_run: bool = False,
) -> AsyncGenerator[PodcastGenTask, None]:
    if not languages:
        languages = generator_config.languages
    if languages:
        languages = [lang.lower() for lang in languages]

    if not length_str:
        length_str = generator_config.length

    length_str = resolve_duration(length_str or "default")

    plugin = load_plugin(type_name)
    inputs = plugin.Inputs.model_validate(format_args)

    async with get_notebooklm_client(notebooklm_config) as client:
        critical_path = Path(__file__).parent / "data" / "critical.md"
        critical_content = critical_path.read_text()
        params = AudioGenParams(notebook_id=notebook_id, length=length_str)
        plugin_prompt = await plugin.get_prompt(client, inputs, params)
        instructions = f"{critical_content}\n\n{plugin_prompt}"

        if dry_run:
            return

        minutes = parse_duration_minutes(length_str) or DEFAULT_AUDIO_MINUTES
        eta = max(480.0, minutes * 30.0)

        async def create_for_language(lang_code: str) -> PodcastGenTask:
            audio_length = duration_to_audio_length(length_str)
            if lang_code not in LANGUAGES_SUPPORTING_LENGTH:
                audio_length = min(audio_length, AudioLength.DEFAULT)

            try:
                status = await client.artifacts.generate_audio(
                    notebook_id,
                    language=lang_code,
                    instructions=instructions,
                    audio_length=audio_length,
                )
                if status.status == TaskStatus.FAILED or not status.task_id:
                    raise RuntimeError(status.error or "No task ID returned")
                return PodcastGenTask(
                    notebook_id=notebook_id,
                    task_id=status.task_id,
                    eta=eta,
                    generation_started_at=time.time(),
                    metadata={
                        "generate-podcast": {
                            "language": lang_code,
                            "type": type_name,
                            "length": length_str,
                            "format_args": inputs.model_dump(),
                        }
                    },
                )
            except Exception as e:
                logger.error(
                    f"[{lang_code}] Audio generation initialization failed: {e}"
                )
                return PodcastGenTask(
                    notebook_id=notebook_id,
                    task_id="",
                    status=TaskStatus.FAILED,
                    error=str(e),
                    metadata={
                        "generate-podcast": {
                            "language": lang_code,
                            "type": type_name,
                            "length": length_str,
                            "format_args": inputs.model_dump(),
                        }
                    },
                )

        for lang_code in languages:
            yield await create_for_language(lang_code)


def _poll_interval(t: float, target: float) -> float:
    """Compute the next polling interval based on elapsed time since generation started.

    Two-piece curve with peak polling frequency at the ETA:
      Pre-ETA:  linear ramp from INITIAL_POLL_INTERVAL down to MIN_POLL_INTERVAL
      Post-ETA: exponential growth from MIN_POLL_INTERVAL, capped at MAX_POLL_INTERVAL
    """
    if t <= target:
        progress = t / target if target > 0 else 1.0
        return (
            INITIAL_POLL_INTERVAL
            - (INITIAL_POLL_INTERVAL - MIN_POLL_INTERVAL) * progress
        )
    else:
        rate = math.log(MAX_POLL_INTERVAL / MIN_POLL_INTERVAL) / POST_ETA_HALF_LIFE
        raw = MIN_POLL_INTERVAL * math.exp(rate * (t - target))
        return min(MAX_POLL_INTERVAL, raw)


async def _poll_single_task(
    client: RetryingNotebookLMClient,
    notebook_id: str,
    task_id: str,
    lang_code: str,
    target_time: float = 600.0,
    generation_started_at: Optional[float] = None,
) -> dict:
    """Polls a single artifact generation task until complete, failed, or timed out.

    Uses a two-piece interval curve that peaks at the ETA. If generation_started_at
    is provided (e.g. from a resumed workflow), polling starts at the correct offset
    on the curve with an immediate first poll.
    """
    if generation_started_at is not None:
        started_at = generation_started_at
    else:
        started_at = time.time()

    while True:
        try:
            generation_status = await client.artifacts.poll_status(notebook_id, task_id)
            logger.debug(
                "[%s] Task %s status: %s",
                lang_code,
                task_id,
                generation_status.status,
            )

            if generation_status.is_complete:
                artifacts = await client.artifacts.list(notebook_id)
                artifact = next(
                    (item for item in artifacts or [] if item.id == task_id), None
                )
                return {
                    "status": TaskStatus.COMPLETED,
                    "notebook_id": notebook_id,
                    "artifact_id": task_id,
                    "title": getattr(artifact, "title", None),
                    "created_at": (
                        artifact.created_at.isoformat()
                        if artifact and getattr(artifact, "created_at", None)
                        else None
                    ),
                }

            if generation_status.is_failed or generation_status.is_removed:
                error_msg = generation_status.error or (
                    f"NotebookLM artifact status is '{generation_status.status}'"
                )
                return {
                    "status": TaskStatus.FAILED,
                    "notebook_id": notebook_id,
                    "artifact_id": task_id,
                    "error": error_msg,
                }
        except Exception as e:
            if not is_transient_network_exception(e):
                logger.error(
                    f"[{lang_code}] Non-retryable polling error for task {task_id}: {e}"
                )
                return {
                    "status": TaskStatus.FAILED,
                    "notebook_id": notebook_id,
                    "artifact_id": task_id,
                    "error": str(e),
                }
            logger.warning(
                f"[{lang_code}] Transient network error polling task {task_id}: {e}"
            )

        t = time.time() - started_at
        if t > MAX_POLL_TIMEOUT_SECONDS:
            logger.warning(f"[{lang_code}] Task {task_id} timed out after {t:.1f}s")
            return {
                "status": TaskStatus.FAILED,
                "notebook_id": notebook_id,
                "artifact_id": task_id,
                "error": f"Task {task_id} timed out after {t:.1f} seconds",
            }

        interval = _poll_interval(t, target_time)
        logger.debug(
            f"[{lang_code}] Task {task_id} next poll in {interval:.0f}s (t={t:.0f}s, target={target_time:.0f}s)"
        )
        await asyncio.sleep(interval)


async def poll_tasks(
    tasks: AsyncIterable[PodcastGenTask],
    notebooklm_config: NotebookLMConfig,
) -> AsyncGenerator[PodcastGenTask, None]:
    async with get_notebooklm_client(notebooklm_config) as client:
        pending = set()

        async def wrap_poll(t_item: PodcastGenTask):
            lang_code = t_item.metadata.get("generate-podcast", {}).get(
                "language", "unknown"
            )
            res = await _poll_single_task(
                client,
                t_item.notebook_id,
                t_item.task_id,
                lang_code,
                t_item.eta,
                t_item.generation_started_at,
            )
            if res:
                metadata = t_item.metadata.copy()
                metadata["poll-artifact-task"] = {
                    "task_id": t_item.task_id,
                    "status": res.get("status", TaskStatus.COMPLETED),
                    "polled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                status_val = res.get("status", TaskStatus.COMPLETED)
                return PodcastGenTask(
                    notebook_id=t_item.notebook_id,
                    task_id=t_item.task_id,
                    title=res.get("title") or t_item.title,
                    status=status_val,
                    error=res.get("error"),
                    metadata=metadata,
                )
            return t_item

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
    artifacts: AsyncIterable[Union[PodcastGenTask, PodcastGenArtifact]],
    working_dir: str,
    notebooklm_config: NotebookLMConfig,
) -> AsyncGenerator[PodcastGenArtifact, None]:
    os.makedirs(working_dir, exist_ok=True)

    async with get_notebooklm_client(notebooklm_config) as client:
        async for raw_art in artifacts:
            if isinstance(raw_art, PodcastGenArtifact):
                art_item = raw_art
            else:
                art_item = PodcastGenArtifact(
                    notebook_id=raw_art.notebook_id,
                    artifact_id=raw_art.task_id,
                    title=raw_art.title or raw_art.task_id,
                    path="",
                    filename="",
                    metadata=raw_art.metadata,
                )

            notebook_id = art_item.notebook_id
            artifact_id = art_item.artifact_id
            title = art_item.title

            os.makedirs(working_dir, exist_ok=True)

            # Filename based on title suffixed with id
            safe_title = sanitize(title)
            filename = f"{safe_title} [{artifact_id}].m4a"
            out_path = os.path.join(working_dir, filename)

            try:
                logger.debug(f"Downloading {artifact_id} to {out_path}...")
                await client.artifacts.download_audio(
                    notebook_id, out_path, artifact_id=artifact_id
                )

                yield PodcastGenArtifact(
                    notebook_id=notebook_id,
                    artifact_id=artifact_id,
                    title=title,
                    path=out_path,
                    filename=filename,
                    lrc_path=art_item.lrc_path,
                    transcript_path=art_item.transcript_path,
                    metadata=art_item.metadata,
                )
            except Exception as e:
                raise RuntimeError(
                    f"Failed to download artifact {artifact_id}: {e}"
                ) from e
