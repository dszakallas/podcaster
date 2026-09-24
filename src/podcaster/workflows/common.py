"""Shared DBOS steps and workflow helpers across workflow plugins."""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

from dbos import DBOS

from podcaster import cover, tagging, transcription
from podcaster.audio_gen import core as audio_gen_core
from podcaster.config import (
    DistributionConfig,
    GCPConfig,
    GenerateCoverSpecConfig,
    NotebookLMConfig,
    PodcastTagsConfig,
    PodcastTranscriptionConfig,
    TaggingConfig,
)
from podcaster.models import (
    PodcastGenArtifact,
    PodcastGenTask,
    TaskStatus,
)
from podcaster.utils import async_iter
from podcaster.utils.logging import log_task
from podcaster.utils.notebooklm import get_notebooklm_client
from podcaster.utils.retry import is_transient_network_exception
from podcaster.workflows.notifications import WorkflowDistributionMetadata

logger = logging.getLogger(__name__)


@DBOS.step()
async def generate_cover_step(
    notebook_id: str,
    working_dir: str,
    notebooklm_config: NotebookLMConfig,
    cover_spec: GenerateCoverSpecConfig,
    retry_count: int = 3,
) -> str:
    """Generate a cover, retrying only known existing jobs."""
    async with log_task(
        "generate_cover_step",
        logger,
        notebook_id=notebook_id,
        working_dir=working_dir,
    ):
        task_id: str | None = None
        image_gen_prompt: str | None = None
        prev_failed_task_id: str | None = None

        async def record_created_job(created_task_id: str, prompt: str) -> None:
            nonlocal task_id, image_gen_prompt
            task_id = created_task_id
            image_gen_prompt = prompt

        async with get_notebooklm_client(notebooklm_config) as client:
            attempts = 0
            while True:
                try:
                    return await cover.generate_cover_for_notebook(
                        notebook_id=notebook_id,
                        working_dir=working_dir,
                        notebooklm_client=client,
                        model=cover_spec.model,
                        task_id=task_id,
                        image_gen_prompt=image_gen_prompt,
                        on_start_callback=record_created_job,
                    )
                except Exception as e:
                    if attempts < retry_count:
                        attempts += 1
                        logger.warning(
                            "Cover job %s failed: %s. Retrying job (attempt %s/%s)...",
                            task_id or "creation",
                            e,
                            attempts,
                            retry_count,
                        )
                        if (
                            isinstance(e, cover.CoverJobTerminalError)
                            or prev_failed_task_id == task_id
                        ):
                            task_id = None
                            image_gen_prompt = None
                        prev_failed_task_id = task_id
                        await asyncio.sleep(2**attempts)
                        continue
                    raise


@DBOS.step()
async def poll_audio_tasks_step(
    tasks: list[PodcastGenTask], notebooklm_config: NotebookLMConfig
) -> list[PodcastGenTask]:
    """Wait for all podcast-generation tasks to complete concurrently."""
    if not tasks:
        return []

    completed_map: dict[str, PodcastGenTask] = {}
    failed_errors: list[str] = []
    async for completed_task in audio_gen_core.poll_tasks(
        async_iter(tasks), notebooklm_config=notebooklm_config
    ):
        if completed_task.status == TaskStatus.FAILED:
            failed_errors.append(
                completed_task.error
                or f"Podcast generation failed for task {completed_task.task_id}"
            )
        completed_map[completed_task.task_id] = completed_task

    if failed_errors:
        raise RuntimeError("; ".join(failed_errors))

    results: list[PodcastGenTask] = []
    for task in tasks:
        completed = completed_map.get(task.task_id)
        if not completed:
            raise RuntimeError(f"Task failed to complete: {task.task_id}")
        results.append(completed)
    return results


async def poll_audio_task_step(
    task_info: PodcastGenTask, notebooklm_config: NotebookLMConfig
) -> PodcastGenTask:
    """Wait for one podcast-generation task to complete."""
    completed_tasks = await poll_audio_tasks_step([task_info], notebooklm_config)
    return completed_tasks[0]


@DBOS.step()
async def download_audio_artifact_step(
    task_info: PodcastGenTask, working_dir: str, notebooklm_config: NotebookLMConfig
) -> PodcastGenArtifact:
    """Download one completed podcast-generation task."""
    os.makedirs(working_dir, exist_ok=True)

    async for artifact in audio_gen_core.download_artifacts(
        async_iter(task_info),
        working_dir=working_dir,
        notebooklm_config=notebooklm_config,
    ):
        return artifact
    raise RuntimeError(f"Failed to download artifact {task_info.task_id}")


@DBOS.step()
async def tag_audio_artifact_step(
    artifact: PodcastGenArtifact,
    cover_image_path: str | None,
    album: str | None,
    created_at: str | None,
    tags_config: PodcastTagsConfig,
    track: int | None = None,
    total_tracks: int | None = None,
) -> PodcastGenArtifact:
    """Apply ID3 metadata to one downloaded podcast artifact."""
    track_to_use = track if track is not None else artifact.metadata.get("track")
    total_tracks_to_use = (
        total_tracks
        if total_tracks is not None
        else artifact.metadata.get("total_tracks")
    )
    track_offset = (int(track_to_use) - 1) if track_to_use is not None else 0
    total_tracks_int = (
        int(total_tracks_to_use) if total_tracks_to_use is not None else None
    )
    async for tagged_artifact in tagging.tag_artifacts(
        async_iter(artifact),
        cover_path=cover_image_path,
        track_offset=track_offset,
        album=album,
        created_at=created_at,
        tags_config=tags_config,
        total_tracks=total_tracks_int,
    ):
        return tagged_artifact
    raise RuntimeError(f"Failed to tag artifact {artifact.artifact_id}")


@DBOS.step()
async def transcribe_audio_artifact_step(
    artifact: PodcastGenArtifact,
    retry_count: int,
    transcription_config: PodcastTranscriptionConfig,
    gcp_config: GCPConfig,
) -> PodcastGenArtifact:
    """Transcribe one artifact, failing the workflow if every attempt fails."""
    last_error: Exception | None = None
    for attempt in range(retry_count + 1):
        try:
            async for result in transcription.transcribe_artifacts(
                async_iter(artifact),
                gcp_config=gcp_config,
                transcription_config=transcription_config,
            ):
                transcribed_artifact = artifact.model_copy(
                    update={
                        "lrc_path": result.lrc_path,
                        "transcript_path": result.transcript_path,
                        "metadata": result.metadata,
                    }
                )
                if transcribed_artifact.lrc_path and os.path.exists(
                    transcribed_artifact.lrc_path
                ):
                    return transcribed_artifact
                raise RuntimeError("Transcription completed without an LRC file")
            raise RuntimeError("Transcription did not produce a result")
        except Exception as exc:
            last_error = exc
            if not is_transient_network_exception(exc):
                raise
            if attempt == retry_count:
                break
            logger.warning(
                "Transient transcription failure; retrying (%s/%s): %s",
                attempt + 1,
                retry_count + 1,
                exc,
            )
            await asyncio.sleep(2 ** (attempt + 1))

    raise RuntimeError(
        f"Transcription failed after {retry_count + 1} attempts: {last_error}"
    ) from last_error


@dataclass
class WorkflowNotebookContext:
    """Notebook identity and working directory for workflow execution."""

    notebook_id: str
    title: str
    created_at: str | None = None
    working_dir: str = ""


@dataclass
class AudioProcessingOptions:
    """Options and service configurations for audio artifact processing."""

    notebooklm_config: NotebookLMConfig
    cover_image_path: str | None = None
    tagging_config: TaggingConfig | None = None
    transcribe: bool = False
    transcription_languages: list[str] | None = None
    transcribe_retry_count: int = 1
    transcription_config: PodcastTranscriptionConfig | None = None
    gcp_config: GCPConfig | None = None


@dataclass
class WorkflowEnvironment:
    """Runtime environment configuration for workflow execution."""

    workdir: str
    workflow_id: str
    notebooklm_config: NotebookLMConfig
    gcp_config: GCPConfig | None = None


async def process_audio_tasks(
    context: WorkflowNotebookContext,
    audio_tasks: list[PodcastGenTask],
    options: AudioProcessingOptions,
) -> list[PodcastGenArtifact]:
    """Orchestrate polling and artifact processing deterministically."""
    completed_tasks = await poll_audio_tasks_step(
        tasks=audio_tasks,
        notebooklm_config=options.notebooklm_config,
    )

    processed_artifacts: list[PodcastGenArtifact] = []
    for task in completed_tasks:
        language = (
            task.metadata.get("generate-podcast", {}).get("language", "en")
            if task.metadata
            else "en"
        )
        track_val = task.metadata.get("track")
        total_tracks_val = task.metadata.get("total_tracks")
        async with log_task(
            "process_audio_artifact",
            logger,
            notebook_id=context.notebook_id,
            task_id=task.task_id,
            language=language,
            track=track_val,
        ):
            artifact = await download_audio_artifact_step(
                task, context.working_dir, options.notebooklm_config
            )

            if track_val is not None:
                artifact.metadata["track"] = track_val
            if total_tracks_val is not None:
                artifact.metadata["total_tracks"] = total_tracks_val

            if options.tagging_config and options.tagging_config.enable:
                if not isinstance(options.tagging_config.spec, PodcastTagsConfig):
                    raise ValueError(
                        "Tagging configuration must be resolved before use"
                    )
                artifact = await tag_audio_artifact_step(
                    artifact,
                    options.cover_image_path,
                    context.title,
                    context.created_at,
                    options.tagging_config.spec,
                    track=track_val,
                    total_tracks=total_tracks_val,
                )
            if options.transcribe and (
                options.transcription_languages is None
                or language in options.transcription_languages
            ):
                if options.transcription_config is None or options.gcp_config is None:
                    raise ValueError(
                        "Transcription requires GCP and transcriber configuration"
                    )
                artifact = await transcribe_audio_artifact_step(
                    artifact,
                    options.transcribe_retry_count,
                    options.transcription_config,
                    options.gcp_config,
                )

            processed_artifacts.append(artifact)

    return processed_artifacts


async def process_single_audio_task_step(
    context: WorkflowNotebookContext,
    task_info: PodcastGenTask,
    options: AudioProcessingOptions,
) -> PodcastGenArtifact:
    """Orchestrate the focused DBOS steps required for one podcast artifact."""
    artifacts = await process_audio_tasks(
        context=context,
        audio_tasks=[task_info],
        options=options,
    )
    return artifacts[0]


@DBOS.step()
async def distribute_step(
    target: DistributionConfig,
    working_dir: str,
    metadata: dict | None = None,
):
    """DBOS Step to distribute podcast workflow output."""
    from ..distribution import build_distribution

    async with log_task(
        "distribute_step",
        logger,
        working_dir=working_dir,
    ):
        dist_obj = build_distribution(target)
        await dist_obj.distribute(
            working_dir=working_dir,
            metadata=metadata,
        )


async def cancel_tasks(*tasks: asyncio.Future[Any] | None) -> None:
    """Cancel and await running background tasks, ignoring cancellations and exceptions."""
    running = [t for t in tasks if t is not None and not t.done()]
    for t in running:
        t.cancel()
    if running:
        await asyncio.gather(*running, return_exceptions=True)


async def distribute_workflow_results(
    working_dir: str,
    dist_meta: WorkflowDistributionMetadata,
    distribute_configs: list[Any],
) -> None:
    """Persist distribution metadata and concurrently execute all configured distributions."""
    wf_meta = dist_meta.model_dump()
    meta_file = os.path.join(working_dir, "metadata.json")
    try:
        with open(meta_file, "w", encoding="utf-8") as f:
            f.write(dist_meta.model_dump_json(indent=2))
    except Exception as e:
        logger.warning("Failed to save metadata.json in %s: %s", working_dir, e)

    targets: list[DistributionConfig] = []
    for target in distribute_configs:
        if not isinstance(target, DistributionConfig):
            raise ValueError(f"Invalid distribution configuration: {target}")
        targets.append(target)

    await asyncio.gather(
        *(distribute_step(target, working_dir, metadata=wf_meta) for target in targets)
    )
