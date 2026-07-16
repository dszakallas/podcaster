import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Awaitable, Optional, Union

from podcaster import cover, plex, research, tagging, transcription
from podcaster import notebook as notebook_mod
from podcaster.audio_gen import core as audio_gen_core
from podcaster.utils import (
    DEFAULT_PODCAST_DIR,
    find_notebook_dir,
    load_config,
    log_task,
    resolve_duration,
    task,
)

from .state import TaskState, WorkflowConfig, WorkflowState

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


def _distribution_target_params(target) -> dict:
    return {
        "type": target.type,
        "ref": target.ref,
        "destination": getattr(target, "destination", None),
    }


def _resolve_rsync_distribution_target(
    target, config
) -> tuple[str, Optional[str], Optional[list[str]]]:
    dest = target.destination
    method = target.method
    rclone_flags = target.rclone_flags
    if target.ref:
        ref_config = config.rsync.get(target.ref)
        if not ref_config:
            raise ValueError(
                f"Rsync reference '{target.ref}' not found in top-level config."
            )
        dest = ref_config.destination
        method = ref_config.method
        rclone_flags = ref_config.rclone_flags

    if not dest:
        raise ValueError("Rsync distribution target missing destination.")

    return dest, method, rclone_flags


def _resolve_plex_distribution_target(
    target, config
) -> tuple[str, Optional[str], Optional[str], str, Optional[list[str]]]:
    section_id = target.section_id
    server_library_path = target.server_library_path
    rsync_dest = target.destination
    rsync_method = target.method or "rsync"
    rclone_flags = target.rclone_flags

    if target.ref:
        ref_config = config.plex.get(target.ref)
        if not ref_config:
            raise ValueError(
                f"Plex reference '{target.ref}' not found in top-level config."
            )
        section_id = ref_config.section_id
        server_library_path = ref_config.server_library_path
        if ref_config.rsync and ref_config.rsync.enabled:
            if ref_config.rsync.ref:
                rsync_ref = config.rsync.get(ref_config.rsync.ref)
                if not rsync_ref:
                    raise ValueError(
                        f"Rsync reference '{ref_config.rsync.ref}' (from Plex '{target.ref}') not found."
                    )
                rsync_dest = rsync_ref.destination
                rsync_method = rsync_ref.method
                if not rclone_flags:
                    rclone_flags = rsync_ref.rclone_flags
            else:
                rsync_dest = ref_config.rsync.destination
                rsync_method = ref_config.rsync.method or "rsync"
                if not rclone_flags:
                    rclone_flags = ref_config.rsync.rclone_flags

    if section_id is None:
        raise ValueError("Plex distribution target missing section_id.")

    return section_id, server_library_path, rsync_dest, rsync_method, rclone_flags


async def _run_distribution_target(
    target,
    config,
    notebook_id: str,
    podcast_dir: Optional[str],
    verbose: bool,
):
    target_params = _distribution_target_params(target)
    try:
        async with log_task(
            "distribute_task",
            logger,
            notebook_id=notebook_id,
            target=target_params,
        ):
            if target.type == "rsync":
                dest, method, rclone_flags = _resolve_rsync_distribution_target(
                    target, config
                )
                await plex.sync_podcast(
                    notebook_id=notebook_id,
                    destination=dest,
                    method=method,
                    podcast_dir=podcast_dir,
                    verbose=verbose,
                    rclone_flags=rclone_flags,
                )
            elif target.type == "plex":
                (
                    section_id,
                    server_library_path,
                    rsync_dest,
                    rsync_method,
                    rclone_flags,
                ) = _resolve_plex_distribution_target(target, config)
                await plex.sync_to_plex(
                    notebook_id=notebook_id,
                    plex_section_id=section_id,
                    podcast_dir=podcast_dir,
                    server_library_path=server_library_path,
                    rsync_destination=rsync_dest,
                    sync_method=rsync_method,
                    verbose=verbose,
                    rclone_flags=rclone_flags,
                )
    except Exception:
        pass


@task("upload_source", logger)
async def upload_and_wait_source(
    notebook_id: str, source_file: str, title: Optional[str] = None
) -> str:
    """Uploads a source file or URL and waits for processing."""
    return await notebook_mod.upload_source(notebook_id, source_file, title=title)


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
            if not completed_task:
                raise RuntimeError(f"Task failed to complete: {task_id}")
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
    if _should_skip_tagging(t_state):
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
                            ):
                                tagged = transcribed
                                success = True
                                break
                    except Exception as e:
                        logger.error(f"Error during transcription attempt: {e}")

                    if (
                        success
                        and tagged.get("lrc_path")
                        and os.path.exists(tagged["lrc_path"])
                    ):
                        update_task_state(
                            "transcribed", lrc_path=tagged.get("lrc_path")
                        )
                        break
                    else:
                        error_msg = f"Transcription attempt {attempts + 1} failed or produced no LRC file."
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

    transcription_langs = wf_config.transcribe.spec.languages

    gen_config = config.podcast_generation
    if not length:
        length = gen_config.length
    if not languages:
        languages = gen_config.languages

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
            title=title, podcast_dir=podcast_dir, from_source=source_file
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
        state.cover.status != "completed"
        or not state.cover_image_path
        or not os.path.exists(state.cover_image_path)
    )
    if cover_needed:

        async def on_cover_start(task_id: str, image_gen_prompt: str):
            state.cover.status = "in_progress"
            state.cover.task_id = task_id
            state.cover.image_gen_prompt = image_gen_prompt
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
                        task_id_to_use = state.cover.task_id if attempts == 0 else None
                        prompt_to_use = (
                            state.cover.image_gen_prompt if attempts == 0 else None
                        )

                        cover_path = await cover.generate_cover(
                            notebook_id,
                            podcast_dir=podcast_dir,
                            task_id=task_id_to_use,
                            image_gen_prompt=prompt_to_use,
                            on_start_callback=on_cover_start,
                        )
                        state.cover_image_path = cover_path
                        state.cover.status = "completed"
                        state.save(notebook_dir_path)
                        return cover_path
                except Exception as e:
                    if attempts < retry_count:
                        attempts += 1
                        logger.warning(
                            f"Cover generation failed: {e}. Retrying cover generation task (attempt {attempts}/{retry_count})..."
                        )
                        state.cover.task_id = None
                        state.cover.image_gen_prompt = None
                        state.cover.status = "failed"
                        state.save(notebook_dir_path)
                        continue
                    else:
                        raise

        cover_task = asyncio.create_task(make_cover())

    cover_arg = cover_task if cover_needed else state.cover_image_path

    # We need enrichment if explicitly requested OR if auto-length is needed
    # AND we have not already marked enrichment as completed
    enrichment_needed = (enrich_web or length == "auto") and (
        state.enrichment.status != "completed"
    )

    async def on_enrich_start(
        task_id: str, topic: str, summary: str, suggested_duration: str
    ):
        state.enrichment.status = "in_progress"
        state.enrichment.task_id = task_id
        state.enrichment.topic = topic
        state.enrichment.summary = summary
        state.enrichment.suggested_length = suggested_duration
        state.save(notebook_dir_path)

    research_res = None
    if enrichment_needed:
        max_imports = enrich_config.spec.max_imports if enrich_web else 0
        if max_imports == -1:
            max_imports = None
        mode = enrich_config.spec.mode
        ignore_errors = enrich_config.spec.ignore_errors

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
                task_id=state.enrichment.task_id,
                topic=state.enrichment.topic,
                summary=state.enrichment.summary,
                suggested_duration=state.enrichment.suggested_length,
                on_start_callback=on_enrich_start,
                ignore_errors=ignore_errors,
            )
            state.enrichment.status = "completed"
            state.save(notebook_dir_path)

    if length == "auto":
        # Check if we have suggested duration from a completed/in-progress enrichment
        if state.enrichment.suggested_length:
            length = state.enrichment.suggested_length
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
                verbose=verbose,
            )
            for task in tasks
        ]
        processed_files = list(
            await asyncio.gather(*processing_coros, return_exceptions=False)
        )

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
