import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncGenerator, Awaitable, Optional, Union

from podcaster import cover, research, tagging, transcription
from podcaster import notebook as notebook_mod
from podcaster.audio_gen import core as audio_gen_core
from podcaster.config import PodcastTagsConfig, TaggingConfig
from podcaster.models import (
    PodcastGenArtifact,
    PodcastGenTask,
    TaskStatus,
    TranscriptionTask,
)
from podcaster.utils import (
    DEFAULT_PODCAST_DIR,
    find_notebook_dir,
    load_config,
    log_task,
    parse_duration_minutes,
    resolve_duration,
    task,
)

from .state import (
    CoverState,
    EnrichmentState,
    TaskState,
    TranscriptionState,
    WorkflowConfig,
    WorkflowState,
)

logger = logging.getLogger(__name__)


async def _single_artifact_stream(art: Any) -> AsyncGenerator[Any, None]:
    yield art


def _build_completed_task(
    notebook_id: str,
    task_id: Optional[str],
    task_info: PodcastGenTask,
    t_state: TaskState,
) -> PodcastGenTask:
    tid = task_id or ""
    return PodcastGenTask(
        notebook_id=notebook_id,
        task_id=tid,
        title=t_state.title or task_info.title or tid,
        status=TaskStatus.COMPLETED,
        metadata=task_info.metadata,
    )


def _resolve_date_from_dir(
    podcast_dir: Optional[str], notebook_id: str
) -> Optional[str]:
    if not podcast_dir:
        return None
    notebook_dir_name = find_notebook_dir(podcast_dir, notebook_id)
    if notebook_dir_name:
        import re

        m = re.match(r"^(\d{4}-\d{2}-\d{2})", notebook_dir_name)
        if m:
            return m.group(1)
    return None


def _task_state_for(
    task_id: Optional[str], lang: str, state: Optional[WorkflowState]
) -> Optional[TaskState]:
    if not state or not task_id:
        return None
    t_state = next((t for t in state.tasks if t.task_id == task_id), None)
    if t_state:
        return t_state
    return next((t for t in state.tasks if t.language == lang), None)


def _should_skip_existing_audio(t_state: Optional[TaskState]) -> bool:
    return bool(t_state and t_state.audio_path and os.path.exists(t_state.audio_path))


def _should_skip_tagging(t_state: Optional[TaskState]) -> bool:
    return bool(t_state and t_state.is_tagged)


def _should_skip_transcription(
    t_state: Optional[TaskState], transcript_path: Optional[str]
) -> bool:
    return bool(
        t_state
        and t_state.transcription
        and t_state.transcription[-1].status == TaskStatus.COMPLETED
        and transcript_path
        and os.path.exists(transcript_path)
    )


async def _resolve_cover_image(
    cover_image: Optional[Union[str, Awaitable[str]]],
) -> Optional[str]:
    if isinstance(cover_image, str) or cover_image is None:
        return cover_image
    if cover_image and (
        asyncio.isfuture(cover_image)
        or isinstance(cover_image, asyncio.Task)
        or hasattr(cover_image, "__await__")
    ):
        logger.debug("Waiting for background cover generation to complete...")
        resolved = await cover_image
        logger.debug(f"Background cover generation complete. Cover path: {resolved}")
        return resolved
    return None


def _build_generation_task(
    notebook_id: str,
    lang_code: str,
    task_state: TaskState,
    length: str,
    format_args: dict,
) -> PodcastGenTask:
    minutes = parse_duration_minutes(length) or 20
    eta = max(480.0, minutes * 30.0)
    return PodcastGenTask(
        notebook_id=notebook_id,
        task_id=task_state.task_id,
        title=task_state.title,
        eta=eta,
        generation_started_at=task_state.generation_started_at,
        metadata={
            "generate-podcast": {
                "language": lang_code,
                "type": "main-article-with-author",
                "length": length,
                "format_args": format_args,
            }
        },
    )


def _append_generated_state(state: WorkflowState, task: PodcastGenTask) -> None:
    lang_code = task.metadata.get("generate-podcast", {}).get("language")
    t_state = next((t for t in state.tasks if t.language == lang_code), None)
    if not t_state:
        state.tasks.append(
            TaskState(
                task_id=task.task_id,
                language=lang_code or "en",
                status=TaskStatus.IN_PROGRESS,
                title=task.title,
                generation_started_at=task.generation_started_at,
            )
        )
        return

    t_state.task_id = task.task_id
    t_state.status = TaskStatus.IN_PROGRESS
    if task.title:
        t_state.title = task.title
    if task.generation_started_at:
        t_state.generation_started_at = task.generation_started_at


def _build_processed_file(notebook_id: str, t_state: TaskState) -> PodcastGenArtifact:
    audio_path = t_state.audio_path or ""
    return PodcastGenArtifact(
        notebook_id=notebook_id,
        artifact_id=t_state.task_id,
        title=t_state.title or os.path.splitext(os.path.basename(audio_path))[0],
        path=audio_path,
        filename=os.path.basename(audio_path),
        lrc_path=t_state.lrc_path,
        metadata={"generate-podcast": {"language": t_state.language}},
    )


async def _run_distribution_target(
    target,
    notebook_id: str,
    podcast_dir: Optional[str],
):
    from ...distribution import build_distribution

    try:
        async with log_task(
            "distribute_task",
            logger,
            notebook_id=notebook_id,
        ):
            dist_obj = build_distribution(target)
            await dist_obj.distribute(
                notebook_id=notebook_id,
                podcast_dir=podcast_dir,
            )
    except Exception as e:
        logger.error(f"Distribution target failed: {e}", exc_info=True)


@task("upload_source", logger)
async def upload_and_wait_source(
    notebook_id: str,
    source_file: str,
    title: Optional[str] = None,
    importer: Any = "default",
) -> str:
    """Uploads a source file or URL and waits for processing."""
    return await notebook_mod.upload_source(
        notebook_id, source_file, title=title, importer=importer
    )


@task("process_podcast_task", logger)
async def generate_download_and_tag_podcast(
    notebook_id: str,
    task_info: PodcastGenTask,
    cover_image: Optional[Union[str, asyncio.Task, None]],
    state: Optional[WorkflowState] = None,
    notebook_dir: Optional[Path] = None,
    podcast_dir: Optional[str] = None,
    transcribe: bool = False,
    transcription_languages: Optional[list[str]] = None,
    transcribe_retry_count: int = 0,
    transcriber_key: str = "default",
    tagging_config: Optional[TaggingConfig] = None,
):
    """Polls, downloads, and tags a specific generation task once complete."""
    task_id = task_info.task_id
    lang = task_info.metadata.get("generate-podcast", {}).get("language", "en")
    logger.debug(f"Processing task {task_id} for language {lang}...")

    def update_task_state(
        status: Optional[TaskStatus] = None,
        audio_path: Optional[str] = None,
        lrc_path: Optional[str] = None,
        is_tagged: Optional[bool] = None,
        error: Optional[str] = None,
        title: Optional[str] = None,
    ):
        if not state or not notebook_dir:
            return
        t_state = None
        if task_id:
            t_state = next((t for t in state.tasks if t.task_id == task_id), None)
        if not t_state:
            t_state = next((t for t in state.tasks if t.language == lang), None)
        if not t_state:
            t_state = TaskState(
                task_id=task_id or "",
                language=lang,
                status=status or TaskStatus.PENDING,
            )
            state.tasks.append(t_state)

        if task_id:
            t_state.task_id = task_id
        if status:
            t_state.status = status
        if audio_path:
            t_state.audio_path = audio_path
        if lrc_path:
            t_state.lrc_path = lrc_path
        if is_tagged is not None:
            t_state.is_tagged = is_tagged
        if error:
            t_state.error = error
        if title:
            t_state.title = title
        state.save(notebook_dir)

    # Resolve state check
    t_state = _task_state_for(task_id, lang, state)

    # Check if task generation itself failed prior to polling
    if task_info.status == TaskStatus.FAILED:
        err = task_info.error or "Task generation failed to start"
        logger.error(f"[{lang}] Task generation failed: {err}")
        update_task_state(TaskStatus.FAILED, error=err)
        return None

    # Step 1: Poll
    completed_task: Optional[PodcastGenTask] = None
    if t_state and t_state.status == TaskStatus.COMPLETED:
        logger.debug(f"Skipping polling for already completed task: {task_id}")
        completed_task = _build_completed_task(notebook_id, task_id, task_info, t_state)
    else:
        async with log_task(
            "poll_task", logger, notebook_id=notebook_id, task_id=task_id, language=lang
        ):

            async def task_gen():
                yield task_info

            async for completed in audio_gen_core.poll_tasks(task_gen()):
                completed_task = completed
                break

            if not completed_task or completed_task.status == TaskStatus.FAILED:
                err_msg = (
                    completed_task.error
                    if completed_task
                    else f"Task failed to complete: {task_id}"
                )
                logger.error(f"[{lang}] Audio task failed: {err_msg}")
                update_task_state(TaskStatus.FAILED, error=err_msg)
                raise RuntimeError(err_msg)

            update_task_state(TaskStatus.COMPLETED, title=completed_task.title)

    assert completed_task is not None

    # Step 2: Download
    downloaded: Optional[PodcastGenArtifact] = None
    if _should_skip_existing_audio(t_state) and t_state and t_state.audio_path:
        audio_path_val = t_state.audio_path
        logger.debug(f"Skipping download, file already exists: {audio_path_val}")
        downloaded = PodcastGenArtifact(
            notebook_id=notebook_id,
            artifact_id=completed_task.task_id,
            title=completed_task.title or completed_task.task_id,
            path=audio_path_val,
            filename=os.path.basename(audio_path_val),
            metadata=completed_task.metadata,
        )
    else:
        async with log_task(
            "download_task",
            logger,
            notebook_id=notebook_id,
            artifact_id=completed_task.task_id,
            podcast_dir=podcast_dir,
        ):

            async def completed_gen():
                yield completed_task

            async for down in audio_gen_core.download_artifacts(
                completed_gen(), podcast_dir=podcast_dir
            ):
                downloaded = down
                break
            if not downloaded:
                raise RuntimeError(
                    f"Failed to download artifact {completed_task.task_id}"
                )
            update_task_state(audio_path=downloaded.path)

    assert downloaded is not None

    # Step 3: Tag
    tagged: Optional[PodcastGenArtifact] = None
    if not tagging_config or not tagging_config.enable:
        logger.debug("Tagging is disabled in workflow config, skipping.")
        tagged = downloaded
    elif _should_skip_tagging(t_state):
        logger.debug("Skipping tagging for already tagged file")
        tagged = downloaded
    else:
        resolved_cover_image = await _resolve_cover_image(cover_image)

        async with log_task(
            "tag_task",
            logger,
            notebook_id=notebook_id,
            artifact_id=completed_task.task_id,
            cover_path=resolved_cover_image,
        ):

            async def downloaded_gen():
                yield downloaded

            date_val = _resolve_date_from_dir(podcast_dir, notebook_id)
            async for tag in tagging.tag_artifacts(
                downloaded_gen(),
                cover_path=resolved_cover_image,
                album=state.notebook_title if state else None,
                created_at=date_val,
                tags_config=(
                    tagging_config.spec
                    if tagging_config
                    and isinstance(tagging_config.spec, PodcastTagsConfig)
                    else None
                ),
            ):
                tagged = tag
                break
            if not tagged:
                raise RuntimeError(f"Failed to tag artifact {completed_task.task_id}")
            update_task_state(is_tagged=True)

    assert tagged is not None

    # Step 4: Transcribe
    if transcribe:
        if transcription_languages is None or lang in transcription_languages:
            tagged_path = tagged.path
            transcript_path = os.path.splitext(tagged_path)[0] + ".tr.json"
            if _should_skip_transcription(t_state, transcript_path) and t_state:
                logger.debug("Skipping transcription, LRC file already exists")
                tagged = tagged.model_copy(
                    update={
                        "lrc_path": t_state.lrc_path,
                        "transcript_path": transcript_path,
                    }
                )
            else:
                attempts = 0
                while True:
                    success = False
                    t_state = _task_state_for(task_id, lang, state)

                    existing_tr = (
                        t_state.transcription[-1]
                        if (t_state and t_state.transcription)
                        else None
                    )

                    try:
                        async with log_task(
                            "transcribe_task",
                            logger,
                            artifact_id=completed_task.task_id,
                            language=lang,
                        ):
                            if (
                                existing_tr
                                and existing_tr.task_id
                                and existing_tr.status
                                in (TaskStatus.IN_PROGRESS, TaskStatus.PENDING)
                            ):
                                logger.info(
                                    f"Resuming existing transcription task {existing_tr.task_id} for {lang}"
                                )
                                bcp47_lang = (
                                    transcription.LANGUAGE_MAP.get(lang, lang)
                                    or "en-US"
                                )
                                created_job = TranscriptionTask(
                                    artifact_id=completed_task.task_id,
                                    task_id=existing_tr.task_id,
                                    gcs_uri=existing_tr.gcs_uri,
                                    path=tagged.path,
                                    bcp47_lang=bcp47_lang,
                                    status=existing_tr.status,
                                )
                                created_job_stream = _single_artifact_stream(
                                    created_job
                                )
                            else:
                                if t_state and state and notebook_dir:
                                    t_state.transcription.append(
                                        TranscriptionState(
                                            status=TaskStatus.IN_PROGRESS
                                        )
                                    )
                                    state.save(notebook_dir)

                                async def _create_and_save():
                                    async for (
                                        job
                                    ) in transcription.create_transcription_jobs(
                                        _single_artifact_stream(tagged),
                                        transcriber_key=transcriber_key,
                                    ):
                                        current_t = _task_state_for(
                                            task_id, lang, state
                                        )
                                        if (
                                            current_t
                                            and current_t.transcription
                                            and state
                                            and notebook_dir
                                        ):
                                            current_t.transcription[-1].task_id = (
                                                job.task_id
                                            )
                                            current_t.transcription[-1].gcs_uri = (
                                                job.gcs_uri
                                            )
                                            state.save(notebook_dir)
                                        yield job

                                created_job_stream = _create_and_save()

                            async for created_tr in created_job_stream:
                                async for (
                                    polled_tr
                                ) in transcription.poll_transcription_jobs(
                                    _single_artifact_stream(created_tr)
                                ):
                                    async for (
                                        downloaded_tr
                                    ) in transcription.download_transcription_jobs(
                                        _single_artifact_stream(polled_tr)
                                    ):
                                        tagged = tagged.model_copy(
                                            update={
                                                "lrc_path": downloaded_tr.lrc_path,
                                                "transcript_path": downloaded_tr.transcript_path,
                                                "metadata": downloaded_tr.metadata,
                                            }
                                        )
                                        success = True
                                        break
                                    break
                                break
                    except Exception as e:
                        logger.error(f"Error during transcription attempt: {e}")

                    t_state = _task_state_for(task_id, lang, state)
                    lrc_path_val = tagged.lrc_path
                    if success and lrc_path_val and os.path.exists(lrc_path_val):
                        update_task_state(lrc_path=lrc_path_val)
                        if t_state and t_state.transcription and state and notebook_dir:
                            t_state.transcription[-1].status = TaskStatus.COMPLETED
                            t_state.transcription[-1].lrc_path = lrc_path_val
                            state.save(notebook_dir)
                        break
                    else:
                        error_msg = f"Transcription attempt {attempts + 1} failed or produced no LRC file."
                        if t_state and t_state.transcription and state and notebook_dir:
                            t_state.transcription[-1].status = TaskStatus.FAILED
                            t_state.transcription[-1].error = error_msg
                            state.save(notebook_dir)
                        if attempts < transcribe_retry_count:
                            attempts += 1
                            logger.warning(
                                f"{error_msg} Retrying transcription task (attempt {attempts}/{transcribe_retry_count})..."
                            )
                            continue
                        else:
                            raise RuntimeError(
                                f"Transcription failed after {attempts} retries: {error_msg}"
                            )
        else:
            logger.debug(
                f"Skipping transcription for {completed_task.task_id} (lang: {lang} not in {transcription_languages})"
            )

    return tagged


@task("workflow_run", logger)
async def run(
    preset_name: str,
    title: Optional[str] = None,
    source_file: Optional[str] = None,
    notebook_id: Optional[str] = None,
    length: Optional[str] = None,
    languages: Optional[list[str]] = None,
    enrich_web: Optional[bool] = None,
    generate_cover: Optional[bool] = None,
    transcribe: Optional[bool] = None,
    podcast_dir: Optional[str] = None,
    resume: bool = False,
):
    if resume:
        if not notebook_id:
            raise ValueError("Must provide notebook_id when resuming a workflow.")
        if title or source_file:
            raise ValueError(
                "Cannot provide title or source_file when resuming a workflow."
            )
    else:
        if notebook_id:
            raise ValueError(
                "Cannot provide notebook_id when starting a new workflow run. Did you mean to use workflow resume?"
            )
        if not source_file:
            raise ValueError(
                "Must provide source_file when starting a new workflow run."
            )

    config = load_config()

    # 1. Resolve podcast directory
    if not podcast_dir:
        podcast_dir = config.podcast_dir or DEFAULT_PODCAST_DIR

    state = None
    notebook_dir_path = None
    source_id = None

    if resume:
        if not notebook_id:
            raise ValueError("Must provide notebook_id when resuming a workflow.")
        # Find directory
        notebook_dir_name = find_notebook_dir(podcast_dir, notebook_id)
        if not notebook_dir_name:
            raise ValueError(f"Could not find directory for notebook ID: {notebook_id}")
        notebook_dir_path = Path(podcast_dir) / notebook_dir_name
        state = WorkflowState.load(notebook_dir_path)
        if not state:
            raise ValueError(
                f"No state.json found for notebook ID {notebook_id} in {notebook_dir_path}"
            )

        # Override configuration parameters with those from the state
        preset_name = state.preset
        length = state.config.length
        languages = state.config.languages
        enrich_web = state.config.enrich_web
        generate_cover = state.config.generate_cover
        transcribe = state.config.transcribe
        source_id = state.source_id

        logger.info(
            f"Resuming workflow for notebook: {notebook_id} using preset: {preset_name}"
        )

    wf_config = config.workflow.root.get(preset_name)
    if not wf_config:
        raise ValueError(f"Workflow preset '{preset_name}' not found.")

    if wf_config.type != "deep_dive_article":
        raise ValueError(f"Unsupported workflow type: {wf_config.type}")

    enrich_config = wf_config.enrich_web

    # Resolve defaults from the specific preset
    if enrich_web is None:
        enrich_web = enrich_config.enable
    if generate_cover is None:
        generate_cover = wf_config.generate_cover.enable
    if transcribe is None:
        transcribe = wf_config.transcribe.enable

    # Resolve podcast transcriber settings (already resolved by load_config)
    transcriber_config = wf_config.transcribe.podcast_transcriber
    transcriber_name = next(
        (k for k, v in config.podcast_transcribers.items() if v is transcriber_config),
        "default",
    )
    transcription_langs = transcriber_config.languages

    # Resolve podcast generator settings (already resolved by load_config)
    generator_config = wf_config.podcast_generator
    generator_name = next(
        (k for k, v in config.podcast_generators.items() if v is generator_config),
        "default",
    )
    gen_languages = generator_config.languages
    gen_length = generator_config.length
    ignore_errors = generator_config.ignore_errors

    if not length:
        length = gen_length
    if not languages:
        languages = gen_languages
    if not languages:
        languages = ["en"]

    if length and length != "auto":
        length = resolve_duration(length)

    logger.info(f"=== Starting Deep Dive Article Workflow (Preset: {preset_name}) ===")

    # Resolve distribution targets
    distribute_targets = wf_config.distribute

    # Initialize Notebook details
    if resume:
        logger.info(f"Resuming existing notebook: {notebook_id}")
    else:
        importer_cfg = wf_config.importer
        importer_name = next(
            (k for k, v in config.importers.items() if v is importer_cfg),
            "default",
        )
        notebook_info = await notebook_mod.init_notebook(
            title=title,
            podcast_dir=podcast_dir,
            from_source=source_file,
            importer=importer_name,
        )
        notebook_id = str(notebook_info["notebook_id"])
        derived_title = str(notebook_info.get("derived_title") or "")
        source_id = str(notebook_info.get("source_id") or "")
        notebook_dir_path = Path(podcast_dir) / notebook_info["local_dir"]

        # Create new state
        wf_cfg = WorkflowConfig(
            length=length,
            languages=languages,
            enrich_web=enrich_web,
            generate_cover=generate_cover,
            transcribe=transcribe,
        )
        state = WorkflowState(
            notebook_id=notebook_id,
            notebook_title=derived_title,
            preset=preset_name,
            config=wf_cfg,
            source_id=source_id,
        )
        state.save(notebook_dir_path)

    assert notebook_id is not None
    assert notebook_dir_path is not None
    assert state is not None
    assert length is not None

    # 2. Handle source
    if resume:
        logger.info(f"Using existing source: {source_id}")

    # Start cover task in the background if needed
    cover_task = None
    cover_needed = generate_cover and (
        not state.cover
        or state.cover[-1].status != TaskStatus.COMPLETED
        or not state.cover_image_path
        or not os.path.exists(state.cover_image_path)
    )
    if cover_needed:

        async def on_cover_start(task_id: str, image_gen_prompt: str):
            if not state.cover or state.cover[-1].status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
            ):
                state.cover.append(
                    CoverState(
                        status=TaskStatus.IN_PROGRESS,
                        task_id=task_id,
                        image_gen_prompt=image_gen_prompt,
                    )
                )
            else:
                state.cover[-1].status = TaskStatus.IN_PROGRESS
                state.cover[-1].task_id = task_id
                state.cover[-1].image_gen_prompt = image_gen_prompt
            state.save(notebook_dir_path)

        async def make_cover():
            retry_count = wf_config.generate_cover.retry_count
            attempts = 0
            while True:
                try:
                    async with log_task(
                        "generate_cover_task",
                        logger,
                        notebook_id=notebook_id,
                        podcast_dir=podcast_dir,
                    ):
                        task_id_to_use = (
                            state.cover[-1].task_id
                            if state.cover and attempts == 0
                            else None
                        )
                        prompt_to_use = (
                            state.cover[-1].image_gen_prompt
                            if state.cover and attempts == 0
                            else None
                        )

                        cover_path = await cover.generate_cover_for_notebook(
                            notebook_id,
                            podcast_dir=podcast_dir,
                            task_id=task_id_to_use,
                            image_gen_prompt=prompt_to_use,
                            on_start_callback=on_cover_start,
                        )
                        state.cover_image_path = cover_path
                        if state.cover:
                            state.cover[-1].status = TaskStatus.COMPLETED
                        state.save(notebook_dir_path)
                        return cover_path
                except Exception as e:
                    if attempts < retry_count:
                        attempts += 1
                        logger.warning(
                            f"Cover generation failed: {e}. Retrying cover generation task (attempt {attempts}/{retry_count})...."
                        )
                        if state.cover:
                            state.cover[-1].status = TaskStatus.FAILED
                            state.cover[-1].error = str(e)
                        state.save(notebook_dir_path)
                        continue
                    else:
                        if state.cover:
                            state.cover[-1].status = TaskStatus.FAILED
                            state.cover[-1].error = str(e)
                        state.save(notebook_dir_path)
                        raise

        cover_task = asyncio.create_task(make_cover())

    cover_arg = cover_task if cover_needed else state.cover_image_path

    # We need enrichment if explicitly requested OR if auto-length is needed
    # AND we have not already marked enrichment as completed
    enrichment_needed = (enrich_web or length == "auto") and (
        not state.enrichment or state.enrichment[-1].status != TaskStatus.COMPLETED
    )

    async def on_enrich_start(
        task_id: str, topic: str, summary: str, suggested_duration: str
    ):
        if not state.enrichment or state.enrichment[-1].status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
        ):
            state.enrichment.append(
                EnrichmentState(
                    status=TaskStatus.IN_PROGRESS,
                    task_id=task_id,
                    topic=topic,
                    summary=summary,
                    suggested_length=suggested_duration,
                )
            )
        else:
            state.enrichment[-1].status = TaskStatus.IN_PROGRESS
            state.enrichment[-1].task_id = task_id
            state.enrichment[-1].topic = topic
            state.enrichment[-1].summary = summary
            state.enrichment[-1].suggested_length = suggested_duration
        state.save(notebook_dir_path)

    research_res = None
    if enrichment_needed:
        max_imports = enrich_config.spec.max_imports if enrich_web else 0
        if max_imports == -1:
            max_imports = None
        mode = enrich_config.spec.mode

        task_id_val = state.enrichment[-1].task_id if state.enrichment else None
        topic_val = state.enrichment[-1].topic if state.enrichment else None
        summary_val = state.enrichment[-1].summary if state.enrichment else None
        suggested_len_val = (
            state.enrichment[-1].suggested_length if state.enrichment else None
        )

        fallback_mech = enrich_config.fallback_mechanism
        if isinstance(fallback_mech, str):
            fallback_str = fallback_mech
        else:
            # fallback_mech is a resolved ImporterConfig
            fallback_str = next(
                (k for k, v in config.importers.items() if v is fallback_mech),
                "default",
            )

        async with log_task(
            "enrich_source_task",
            logger,
            notebook_id=notebook_id,
            source_id=source_id,
            mode=mode,
            max_imports=max_imports,
        ):
            assert source_id is not None
            research_res = await research.research_from_source(
                notebook_id,
                source_id,
                mode=mode,
                max_imports=max_imports,
                task_id=task_id_val,
                topic=topic_val,
                summary=summary_val,
                suggested_duration=suggested_len_val,
                on_start_callback=on_enrich_start,
                fallback_mechanism=fallback_str,
            )
            if state.enrichment:
                state.enrichment[-1].status = TaskStatus.COMPLETED
            state.save(notebook_dir_path)

    if length == "auto":
        # Check if we have suggested duration from a completed/in-progress enrichment
        if state.enrichment and state.enrichment[-1].suggested_length:
            length = state.enrichment[-1].suggested_length
        elif research_res:
            length = research_res.suggested_duration or "20 minutes"
        else:
            length = "20 minutes"  # Fallback

        # Save auto-detected length to state configuration so it persists on resume
        state.config.length = length
        state.save(notebook_dir_path)
        logger.info(f"Auto-detected length: {length}")

    format_args = {"source_id": source_id}

    # 3. Generate podcasts
    tasks = []
    languages_to_generate = []

    for lang_code in languages:
        t_state = next((t for t in state.tasks if t.language == lang_code), None)
        if t_state:
            if (
                t_state.audio_path
                and os.path.exists(t_state.audio_path)
                and (
                    not wf_config.tagging
                    or not wf_config.tagging.enable
                    or t_state.is_tagged
                )
                and (
                    not transcribe
                    or (
                        t_state.transcription
                        and t_state.transcription[-1].status == TaskStatus.COMPLETED
                    )
                )
            ):
                # Completely done
                continue
            if t_state.task_id and t_state.status != TaskStatus.FAILED:
                logger.info(
                    f"Reusing existing task {t_state.task_id} for language {lang_code}"
                )
                tasks.append(
                    _build_generation_task(
                        notebook_id,
                        lang_code,
                        t_state,
                        length or "20 minutes",
                        format_args,
                    )
                )
                continue

        languages_to_generate.append(lang_code)

    if languages_to_generate:
        async with log_task(
            "generate_audio_tasks",
            logger,
            notebook_id=notebook_id,
            languages=languages_to_generate,
            length=length,
        ):
            async for task in audio_gen_core.generate_tasks(
                notebook_id,
                "main-article-with-author",
                languages_to_generate,
                length or "20 minutes",
                json.dumps(format_args),
                dry_run=False,
                generator_key=generator_name,
            ):
                tasks.append(task)
                _append_generated_state(state, task)
        state.save(notebook_dir_path)

    # 4. Poll, download and tag
    processed_files = []
    if tasks:
        # Resolve transcribe_retry_count
        transcribe_retry_count = wf_config.transcribe.retry_count

        processing_coros = [
            generate_download_and_tag_podcast(
                notebook_id,
                task,
                cover_arg,
                state=state,
                notebook_dir=notebook_dir_path,
                podcast_dir=podcast_dir,
                transcribe=transcribe,
                transcription_languages=transcription_langs,
                transcribe_retry_count=transcribe_retry_count,
                transcriber_key=transcriber_name,
                tagging_config=wf_config.tagging,
            )
            for task in tasks
        ]
        results = await asyncio.gather(*processing_coros, return_exceptions=True)
        first_exception = None
        for res in results:
            if isinstance(res, Exception):
                if not first_exception:
                    first_exception = res
                logger.error(f"Audio task error: {res}")
            elif res:
                processed_files.append(res)

        if first_exception and not ignore_errors:
            raise first_exception

    # Add already completed tasks from state to processed_files for reporting
    for t_state in state.tasks:
        if t_state.audio_path and os.path.exists(t_state.audio_path):
            if not any(pf.path == t_state.audio_path for pf in processed_files if pf):
                processed_files.append(_build_processed_file(notebook_id, t_state))

    logger.info(f"Processed podcasts: {[pf.path for pf in processed_files if pf]}")

    # Update status to completed if all tasks are complete
    all_completed = all(
        t.status == TaskStatus.COMPLETED
        and t.audio_path
        and os.path.exists(t.audio_path)
        for t in state.tasks
    )
    if all_completed:
        state.status = TaskStatus.COMPLETED
        state.save(notebook_dir_path)

    # 5. Distribute results
    if distribute_targets:
        logger.info(f"Distributing to {len(distribute_targets)} targets...")

        dist_tasks = [
            _run_distribution_target(target, notebook_id, podcast_dir)
            for target in distribute_targets
        ]
        await asyncio.gather(*dist_tasks)

    if cover_task and not cover_task.done():
        logger.info(
            "Waiting for background cover generation to complete before finishing workflow..."
        )
        await cover_task

    logger.info("=== Workflow Complete ===")
    return {
        "notebook_id": notebook_id,
        "files": [pf.path for pf in processed_files if pf],
    }
