import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Optional, Union

from podcaster import cover, research, tagging, transcription
from podcaster import notebook as notebook_mod
from podcaster.audio_gen import core as audio_gen_core
from podcaster.config import DistributionRef, TaggingConfig
from podcaster.utils import (
    DEFAULT_PODCAST_DIR,
    find_notebook_dir,
    load_config,
    log_task,
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


def _build_completed_task(
    notebook_id: str, task_id: str, task_info: dict, t_state: TaskState
) -> dict:
    return {
        "notebook_id": notebook_id,
        "artifact_id": task_id,
        "title": t_state.title
        or task_info.get("title")
        or (
            os.path.splitext(os.path.basename(t_state.audio_path))[0]
            if t_state.audio_path
            else task_id
        ),
        "metadata": task_info.get("metadata", {}),
    }


def _resolve_date_from_dir(podcast_dir: str, notebook_id: str) -> Optional[str]:
    notebook_dir_name = find_notebook_dir(podcast_dir, notebook_id)
    if notebook_dir_name:
        import re

        m = re.match(r"^(\d{4}-\d{2}-\d{2})", notebook_dir_name)
        if m:
            return m.group(1)
    return None


def _task_state_for(task_id: str, lang: str, state: Optional[WorkflowState]):
    if not state or not task_id:
        return None
    t_state = next((t for t in state.tasks if t.task_id == task_id), None)
    if t_state:
        return t_state
    return next((t for t in state.tasks if t.language == lang), None)


def _should_skip_existing_audio(t_state: Optional[TaskState]) -> bool:
    return bool(
        t_state
        and t_state.status in ("downloaded", "tagged", "transcribed")
        and t_state.audio_path
        and os.path.exists(t_state.audio_path)
    )


def _should_skip_tagging(t_state: Optional[TaskState]) -> bool:
    return bool(t_state and t_state.status in ("tagged", "transcribed"))


def _should_skip_transcription(
    t_state: Optional[TaskState], transcript_path: Optional[str]
) -> bool:
    return bool(
        t_state
        and t_state.status == "transcribed"
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
) -> dict:
    return {
        "notebook_id": notebook_id,
        "task_id": task_state.task_id,
        "title": task_state.title,
        "eta": 10.0,
        "metadata": {
            "generate-podcast": {
                "language": lang_code,
                "type": "main-article-with-author",
                "length": length,
                "format_args": format_args,
            }
        },
    }


def _append_generated_state(state: WorkflowState, task: dict) -> None:
    lang_code = task.get("metadata", {}).get("generate-podcast", {}).get("language")
    t_state = next((t for t in state.tasks if t.language == lang_code), None)
    if not t_state:
        state.tasks.append(
            TaskState(
                task_id=task["task_id"],
                language=lang_code,
                status="generated",
                title=task.get("title"),
            )
        )
        return

    t_state.task_id = task["task_id"]
    t_state.status = "generated"
    if "title" in task:
        t_state.title = task["title"]


def _build_processed_file(notebook_id: str, t_state: TaskState) -> dict:
    return {
        "notebook_id": notebook_id,
        "artifact_id": t_state.task_id,
        "title": t_state.title
        or os.path.splitext(os.path.basename(t_state.audio_path))[0],
        "path": t_state.audio_path,
        "filename": os.path.basename(t_state.audio_path),
        "lrc_path": t_state.lrc_path,
        "metadata": {"generate-podcast": {"language": t_state.language}},
    }


def _distribution_target_params(target: DistributionRef) -> dict:
    if target.ref:
        return {"ref": target.ref}
    return {
        "rsync": getattr(target, "rsync", None),
        "notifiers": getattr(target, "notifiers", []),
    }


async def _run_distribution_target(
    target,
    config,
    notebook_id: str,
    podcast_dir: Optional[str],
    verbose: bool,
):
    from ...distribution import build_distribution

    target_params = _distribution_target_params(target)
    try:
        async with log_task(
            "distribute_task",
            logger,
            notebook_id=notebook_id,
            target=target_params,
        ):
            dist_obj = build_distribution(target, config)
            await dist_obj.distribute(
                notebook_id=notebook_id,
                podcast_dir=podcast_dir,
                verbose=verbose,
            )
    except Exception:
        pass


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
    task_info: dict,
    cover_image: Optional[Union[str, asyncio.Task, None]],
    state: Optional[WorkflowState] = None,
    notebook_dir: Optional[Path] = None,
    podcast_dir: Optional[str] = None,
    track_offset: int = 0,
    transcribe: bool = False,
    transcription_languages: Optional[list[str]] = None,
    transcribe_retry_count: int = 0,
    transcriber_key: str = "default",
    tagging_config: Optional[TaggingConfig] = None,
    verbose: bool = False,
):
    """Polls, downloads, and tags a specific generation task once complete."""
    task_id = task_info.get("task_id") or task_info.get("artifact_id")
    lang = (
        task_info.get("metadata", {}).get("generate-podcast", {}).get("language", "en")
    )
    logger.debug(f"Processing task {task_id} for language {lang}...")

    def update_task_state(
        status: str,
        audio_path: Optional[str] = None,
        lrc_path: Optional[str] = None,
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
            t_state = TaskState(task_id=task_id or "", language=lang, status=status)
            state.tasks.append(t_state)

        if task_id:
            t_state.task_id = task_id
        t_state.status = status
        if audio_path:
            t_state.audio_path = audio_path
        if lrc_path:
            t_state.lrc_path = lrc_path
        if error:
            t_state.error = error
        if title:
            t_state.title = title
        state.save(notebook_dir)

    # Resolve state check
    t_state = _task_state_for(task_id, lang, state)

    # Check if task generation itself failed prior to polling
    if task_info.get("status") == "failed":
        err = task_info.get("error", "Task generation failed to start")
        logger.error(f"[{lang}] Task generation failed: {err}")
        update_task_state("failed", error=err)
        return None

    # Step 1: Poll
    completed_task = None
    if t_state and t_state.status in (
        "completed",
        "downloaded",
        "tagged",
        "transcribed",
    ):
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

            if not completed_task or completed_task.get("status") == "failed":
                err_msg = (
                    completed_task.get("error")
                    if completed_task
                    else f"Task failed to complete: {task_id}"
                )
                logger.error(f"[{lang}] Audio task failed: {err_msg}")
                update_task_state("failed", error=err_msg)
                raise RuntimeError(err_msg)

            update_task_state("completed", title=completed_task.get("title"))

    # Step 2: Download
    downloaded = None
    if _should_skip_existing_audio(t_state):
        logger.debug(f"Skipping download, file already exists: {t_state.audio_path}")
        downloaded = {
            **completed_task,
            "path": t_state.audio_path,
            "filename": os.path.basename(t_state.audio_path),
        }
    else:
        async with log_task(
            "download_task",
            logger,
            notebook_id=notebook_id,
            artifact_id=completed_task["artifact_id"],
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
                    f"Failed to download artifact {completed_task['artifact_id']}"
                )
            update_task_state("downloaded", audio_path=downloaded["path"])

    # Step 3: Tag
    tagged = None
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
            artifact_id=completed_task["artifact_id"],
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
                tags_ref=tagging_config.spec if tagging_config else None,
            ):
                tagged = tag
                break
            if not tagged:
                raise RuntimeError(
                    f"Failed to tag artifact {completed_task['artifact_id']}"
                )
            update_task_state("tagged")

    # Step 4: Transcribe
    if transcribe:
        if transcription_languages is None or lang in transcription_languages:
            transcript_path = os.path.splitext(tagged["path"])[0] + ".tr.json"
            if _should_skip_transcription(t_state, transcript_path):
                logger.debug("Skipping transcription, LRC file already exists")
                tagged = {
                    **tagged,
                    "lrc_path": t_state.lrc_path,
                    "transcript_path": transcript_path,
                }
            else:
                attempts = 0
                while True:
                    success = False
                    t_state = _task_state_for(task_id, lang, state)
                    if t_state:
                        t_state.transcription.append(
                            TranscriptionState(status="in_progress")
                        )
                        state.save(notebook_dir)

                    try:
                        async with log_task(
                            "transcribe_task",
                            logger,
                            artifact_id=completed_task["artifact_id"],
                            language=lang,
                        ):

                            async def tagged_gen():
                                yield tagged

                            async for transcribed in transcription.transcribe_artifacts(
                                tagged_gen(),
                                verbose=verbose,
                                transcriber_key=transcriber_key,
                            ):
                                tagged = transcribed
                                success = True
                                break
                    except Exception as e:
                        logger.error(f"Error during transcription attempt: {e}")

                    t_state = _task_state_for(task_id, lang, state)
                    if (
                        success
                        and tagged.get("lrc_path")
                        and os.path.exists(tagged["lrc_path"])
                    ):
                        update_task_state(
                            "transcribed", lrc_path=tagged.get("lrc_path")
                        )
                        if t_state and t_state.transcription:
                            t_state.transcription[-1].status = "completed"
                            t_state.transcription[-1].lrc_path = tagged.get("lrc_path")
                            state.save(notebook_dir)
                        break
                    else:
                        error_msg = f"Transcription attempt {attempts + 1} failed or produced no LRC file."
                        if t_state and t_state.transcription:
                            t_state.transcription[-1].status = "failed"
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
                                f"Transcription failed after {attempts} retries."
                            )
        else:
            logger.debug(
                f"Skipping transcription for {completed_task['artifact_id']} (lang: {lang} not in {transcription_languages})"
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
    verbose: bool = False,
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

    # Resolve podcast transcriber settings
    transcriber_ref = wf_config.transcribe.podcast_transcriber
    transcriber_name = transcriber_ref.ref or "default"

    from podcaster.config import PodcastTranscriptionConfig

    if transcriber_name in config.podcast_transcribers:
        base_transcriber_config = config.podcast_transcribers[transcriber_name]
    else:
        base_transcriber_config = config.podcast_transcribers.get(
            "default", PodcastTranscriptionConfig()
        )

    transcription_langs = (
        transcriber_ref.languages
        if transcriber_ref.languages is not None
        else base_transcriber_config.languages
    )

    # Resolve podcast generator settings
    generator_ref = wf_config.podcast_generator
    generator_name = generator_ref.ref or "default"

    from podcaster.config import PodcastGenerationConfig

    if generator_name in config.podcast_generators:
        base_gen_config = config.podcast_generators[generator_name]
    else:
        base_gen_config = config.podcast_generators.get(
            "default", PodcastGenerationConfig()
        )

    gen_languages = (
        generator_ref.languages
        if generator_ref.languages is not None
        else base_gen_config.languages
    )
    gen_length = (
        generator_ref.length
        if generator_ref.length is not None
        else base_gen_config.length
    )

    if not length:
        length = gen_length
    if not languages:
        languages = gen_languages
    if not languages:
        languages = ["en"]

    if length != "auto":
        length = resolve_duration(length)

    logger.info(f"=== Starting Deep Dive Article Workflow (Preset: {preset_name}) ===")

    # Resolve distribution targets
    distribute_targets = wf_config.distribute

    # Initialize Notebook details
    if resume:
        logger.info(f"Resuming existing notebook: {notebook_id}")
    else:
        notebook_info = await notebook_mod.init_notebook(
            title=title,
            podcast_dir=podcast_dir,
            from_source=source_file,
            importer=wf_config.importer,
        )
        notebook_id = notebook_info["notebook_id"]
        derived_title = notebook_info["derived_title"]
        source_id = notebook_info["source_id"]
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

    # 2. Handle source
    if resume:
        logger.info(f"Using existing source: {source_id}")

    # Start cover task in the background if needed
    cover_task = None
    cover_needed = generate_cover and (
        not state.cover
        or state.cover[-1].status != "completed"
        or not state.cover_image_path
        or not os.path.exists(state.cover_image_path)
    )
    if cover_needed:

        async def on_cover_start(task_id: str, image_gen_prompt: str):
            if not state.cover or state.cover[-1].status in ("completed", "failed"):
                state.cover.append(
                    CoverState(
                        status="in_progress",
                        task_id=task_id,
                        image_gen_prompt=image_gen_prompt,
                    )
                )
            else:
                state.cover[-1].status = "in_progress"
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

                        cover_path = await cover.generate_cover(
                            notebook_id,
                            podcast_dir=podcast_dir,
                            task_id=task_id_to_use,
                            image_gen_prompt=prompt_to_use,
                            on_start_callback=on_cover_start,
                        )
                        state.cover_image_path = cover_path
                        if state.cover:
                            state.cover[-1].status = "completed"
                        state.save(notebook_dir_path)
                        return cover_path
                except Exception as e:
                    if attempts < retry_count:
                        attempts += 1
                        logger.warning(
                            f"Cover generation failed: {e}. Retrying cover generation task (attempt {attempts}/{retry_count})...."
                        )
                        if state.cover:
                            state.cover[-1].status = "failed"
                            state.cover[-1].error = str(e)
                        state.save(notebook_dir_path)
                        continue
                    else:
                        if state.cover:
                            state.cover[-1].status = "failed"
                            state.cover[-1].error = str(e)
                        state.save(notebook_dir_path)
                        raise

        cover_task = asyncio.create_task(make_cover())

    cover_arg = cover_task if cover_needed else state.cover_image_path

    # We need enrichment if explicitly requested OR if auto-length is needed
    # AND we have not already marked enrichment as completed
    enrichment_needed = (enrich_web or length == "auto") and (
        not state.enrichment or state.enrichment[-1].status != "completed"
    )

    async def on_enrich_start(
        task_id: str, topic: str, summary: str, suggested_duration: str
    ):
        if not state.enrichment or state.enrichment[-1].status in (
            "completed",
            "failed",
        ):
            state.enrichment.append(
                EnrichmentState(
                    status="in_progress",
                    task_id=task_id,
                    topic=topic,
                    summary=summary,
                    suggested_length=suggested_duration,
                )
            )
        else:
            state.enrichment[-1].status = "in_progress"
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
        ignore_errors = enrich_config.spec.ignore_errors

        task_id_val = state.enrichment[-1].task_id if state.enrichment else None
        topic_val = state.enrichment[-1].topic if state.enrichment else None
        summary_val = state.enrichment[-1].summary if state.enrichment else None
        suggested_len_val = (
            state.enrichment[-1].suggested_length if state.enrichment else None
        )

        async with log_task(
            "enrich_source_task",
            logger,
            notebook_id=notebook_id,
            source_id=source_id,
            mode=mode,
            max_imports=max_imports,
        ):
            research_res = await research.research_from_source(
                notebook_id,
                source_id,
                mode=mode,
                max_imports=max_imports,
                verbose=verbose,
                task_id=task_id_val,
                topic=topic_val,
                summary=summary_val,
                suggested_duration=suggested_len_val,
                on_start_callback=on_enrich_start,
                ignore_errors=ignore_errors,
                fallback_mechanism=enrich_config.fallback_mechanism,
            )
            if state.enrichment:
                state.enrichment[-1].status = "completed"
            state.save(notebook_dir_path)

    if length == "auto":
        # Check if we have suggested duration from a completed/in-progress enrichment
        if state.enrichment and state.enrichment[-1].suggested_length:
            length = state.enrichment[-1].suggested_length
        elif research_res:
            length = research_res.get("suggested_duration", "20 minutes")
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
                t_state.status in ("tagged", "transcribed")
                and t_state.audio_path
                and os.path.exists(t_state.audio_path)
            ):
                # Completely done
                continue
            if t_state.task_id:
                logger.info(
                    f"Reusing existing task {t_state.task_id} for language {lang_code}"
                )
                tasks.append(
                    _build_generation_task(
                        notebook_id, lang_code, t_state, length, format_args
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
                length,
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
                verbose=verbose,
            )
            for task in tasks
        ]
        results = await asyncio.gather(*processing_coros, return_exceptions=True)
        for res in results:
            if isinstance(res, Exception):
                logger.warning(f"Audio task error: {res}")
            elif res:
                processed_files.append(res)

    # Add already completed tasks from state to processed_files for reporting
    for t_state in state.tasks:
        if (
            t_state.status in ("tagged", "transcribed")
            and t_state.audio_path
            and os.path.exists(t_state.audio_path)
        ):
            if not any(
                pf.get("path") == t_state.audio_path for pf in processed_files if pf
            ):
                processed_files.append(_build_processed_file(notebook_id, t_state))

    logger.info(f"Processed podcasts: {[pf['path'] for pf in processed_files if pf]}")

    # Update status to completed if all tasks are complete
    all_completed = all(t.status in ("tagged", "transcribed") for t in state.tasks)
    if all_completed:
        state.status = "completed"
        state.save(notebook_dir_path)

    # 5. Distribute results
    if distribute_targets:
        logger.info(f"Distributing to {len(distribute_targets)} targets...")

        dist_tasks = [
            _run_distribution_target(target, config, notebook_id, podcast_dir, verbose)
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
        "files": [pf["path"] for pf in processed_files if pf],
    }
