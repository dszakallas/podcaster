import asyncio
import logging
import os
from typing import Any, AsyncGenerator, List, Optional

from dbos import DBOS

from podcaster import cover, research, tagging, transcription
from podcaster import notebook as notebook_mod
from podcaster.audio_gen import core as audio_gen_core
from podcaster.config import (
    DistributionConfig,
    EnrichWebConfig,
    GCPConfig,
    GenerateCoverSpecConfig,
    ImporterConfig,
    NotebookLMConfig,
    PodcastGenerationConfig,
    PodcastTagsConfig,
    PodcastTranscriptionConfig,
    TaggingConfig,
)
from podcaster.models import (
    PodcastGenArtifact,
    PodcastGenTask,
    TaskStatus,
)
from podcaster.utils.duration import resolve_duration
from podcaster.utils.files import get_workflow_dir
from podcaster.utils.logging import log_task
from podcaster.utils.notebooklm import get_notebooklm_client
from podcaster.utils.retry import is_transient_network_exception

from .config import DeepDiveArticleConfig

logger = logging.getLogger(__name__)


async def _single_artifact_stream(art: Any) -> AsyncGenerator[Any, None]:
    yield art


# --- DBOS STEPS ---


@DBOS.step()
async def init_notebook_step(
    importer_config: ImporterConfig,
    notebooklm_config: NotebookLMConfig,
    title: Optional[str] = None,
    notebook_id: Optional[str] = None,
    from_source: Optional[str] = None,
) -> dict:
    """DBOS Step to initialize or fetch NotebookLM notebook."""
    async with log_task(
        "init_notebook_step",
        logger,
        title=title,
        from_source=from_source,
    ):
        async with get_notebooklm_client(notebooklm_config) as client:
            return await notebook_mod.init_notebook(
                importer=importer_config,
                client=client,
                title=title,
                notebook_id=notebook_id,
                from_source=from_source,
            )


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
        task_id: Optional[str] = None
        image_gen_prompt: Optional[str] = None

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
                    if task_id is not None and attempts < retry_count:
                        attempts += 1
                        logger.warning(
                            "Cover job %s failed: %s. Retrying job (attempt %s/%s)...",
                            task_id,
                            e,
                            attempts,
                            retry_count,
                        )
                        await asyncio.sleep(2**attempts)
                        continue
                    raise


@DBOS.step()
async def enrich_source_step(
    notebook_id: str,
    source_id: str,
    enrich_config: EnrichWebConfig,
    notebooklm_config: NotebookLMConfig,
) -> research.ResearchResult:
    """DBOS Step for web research enrichment."""
    fallback_imp = (
        enrich_config.spec.fallback_importer
        if isinstance(enrich_config.spec.fallback_importer, ImporterConfig)
        else None
    )
    async with log_task(
        "enrich_source_step",
        logger,
        notebook_id=notebook_id,
        source_id=source_id,
        mode=enrich_config.spec.mode,
    ):
        async with get_notebooklm_client(notebooklm_config) as client:
            return await research.research_from_source(
                notebook_id,
                source_id,
                client=client,
                notebooklm_config=notebooklm_config,
                mode=enrich_config.spec.mode,
                fallback_importer=fallback_imp,
                max_import_failures=enrich_config.spec.max_import_failures,
            )


@DBOS.step()
async def create_podcast_audio_jobs_step(
    notebook_id: str,
    languages: List[str],
    length: str,
    source_id: str,
    generator_config: PodcastGenerationConfig,
    notebooklm_config: NotebookLMConfig,
) -> List[PodcastGenTask]:
    """DBOS Step to submit audio generation jobs."""
    async with log_task(
        "create_podcast_audio_jobs_step",
        logger,
        notebook_id=notebook_id,
        languages=languages,
        length=length,
    ):
        tasks = []
        format_args = {"source_id": source_id}
        async for t in audio_gen_core.create_podcast_audio_jobs(
            notebook_id,
            "main-article-with-author",
            languages,
            length,
            format_args,
            generator_config=generator_config,
            notebooklm_config=notebooklm_config,
            dry_run=False,
        ):
            tasks.append(t)
        return tasks


@DBOS.step()
async def poll_audio_task_step(
    task_info: PodcastGenTask, notebooklm_config: NotebookLMConfig
) -> PodcastGenTask:
    """Wait for one podcast-generation task to complete."""

    async def task_gen():
        yield task_info

    async for completed_task in audio_gen_core.poll_tasks(
        task_gen(), notebooklm_config=notebooklm_config
    ):
        if completed_task.status == TaskStatus.FAILED:
            raise RuntimeError(completed_task.error or "Podcast generation failed")
        return completed_task
    raise RuntimeError(f"Task failed to complete: {task_info.task_id}")


@DBOS.step()
async def download_audio_artifact_step(
    task_info: PodcastGenTask, working_dir: str, notebooklm_config: NotebookLMConfig
) -> PodcastGenArtifact:
    """Download one completed podcast-generation task."""
    os.makedirs(working_dir, exist_ok=True)

    async def task_gen():
        yield task_info

    async for artifact in audio_gen_core.download_artifacts(
        task_gen(), working_dir=working_dir, notebooklm_config=notebooklm_config
    ):
        return artifact
    raise RuntimeError(f"Failed to download artifact {task_info.task_id}")


@DBOS.step()
async def tag_audio_artifact_step(
    artifact: PodcastGenArtifact,
    cover_image_path: Optional[str],
    album: Optional[str],
    created_at: Optional[str],
    tags_config: PodcastTagsConfig,
) -> PodcastGenArtifact:
    """Apply ID3 metadata to one downloaded podcast artifact."""
    async for tagged_artifact in tagging.tag_artifacts(
        _single_artifact_stream(artifact),
        cover_path=cover_image_path,
        album=album,
        created_at=created_at,
        tags_config=tags_config,
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
            async for job in transcription.create_transcription_jobs(
                _single_artifact_stream(artifact),
                gcp_config=gcp_config,
                transcription_config=transcription_config,
            ):
                async for polled_task in transcription.poll_transcription_jobs(
                    _single_artifact_stream(job), gcp_config=gcp_config
                ):
                    async for result in transcription.download_transcription_jobs(
                        _single_artifact_stream(polled_task), gcp_config=gcp_config
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
                        raise RuntimeError(
                            "Transcription completed without an LRC file"
                        )
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


async def process_single_audio_task_step(
    notebook_id: str,
    notebook_title: str,
    notebook_created_at: Optional[str],
    task_info: PodcastGenTask,
    cover_image_path: Optional[str],
    working_dir: str,
    transcribe: bool,
    transcription_languages: Optional[List[str]],
    transcribe_retry_count: int,
    transcription_config: Optional[PodcastTranscriptionConfig],
    tagging_config: Optional[TaggingConfig],
    gcp_config: Optional[GCPConfig],
    notebooklm_config: NotebookLMConfig,
) -> PodcastGenArtifact:
    """Orchestrate the focused DBOS steps required for one podcast artifact."""
    language = (
        task_info.metadata.get("generate-podcast", {}).get("language", "en")
        if task_info.metadata
        else "en"
    )
    async with log_task(
        "process_single_audio_task_step",
        logger,
        notebook_id=notebook_id,
        task_id=task_info.task_id,
        language=language,
    ):
        completed_task = await poll_audio_task_step(task_info, notebooklm_config)
        artifact = await download_audio_artifact_step(
            completed_task, working_dir, notebooklm_config
        )

        if tagging_config and tagging_config.enable:
            if not isinstance(tagging_config.spec, PodcastTagsConfig):
                raise ValueError("Tagging configuration must be resolved before use")
            artifact = await tag_audio_artifact_step(
                artifact,
                cover_image_path,
                notebook_title,
                notebook_created_at,
                tagging_config.spec,
            )
        if transcribe and (
            transcription_languages is None or language in transcription_languages
        ):
            if transcription_config is None or gcp_config is None:
                raise ValueError(
                    "Transcription requires GCP and transcriber configuration"
                )
            artifact = await transcribe_audio_artifact_step(
                artifact,
                transcribe_retry_count,
                transcription_config,
                gcp_config,
            )

        return artifact


@DBOS.step()
async def distribute_step(
    target: DistributionConfig,
    working_dir: str,
    metadata: Optional[dict] = None,
):
    """DBOS Step to distribute podcast workflow output."""
    from ...distribution import build_distribution

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


@DBOS.workflow()
async def deep_dive_article_workflow(
    preset_name: str,
    wf_config: DeepDiveArticleConfig,
    workdir: str,
    workflow_id: str,
    title: Optional[str] = None,
    source_url: Optional[str] = None,
    notebook_id: Optional[str] = None,
    length: Optional[str] = None,
    languages: Optional[List[str]] = None,
    enrich_web: Optional[bool] = None,
    generate_cover: Optional[bool] = None,
    transcribe: Optional[bool] = None,
    gcp_config: Optional[GCPConfig] = None,
    notebooklm_config: Optional[NotebookLMConfig] = None,
) -> dict:
    """Durable DBOS Workflow for Deep Dive Article podcast generation."""
    logger.info("Starting workflow %s", workflow_id)
    await DBOS.set_event_async("preset", preset_name)
    await DBOS.set_event_async("workflow_id", workflow_id)
    await DBOS.set_event_async("current_step", "init_notebook")
    if notebooklm_config is None:
        raise ValueError("notebooklm_config is required")

    # Resolve settings from preset config if not explicitly passed
    if enrich_web is None:
        enrich_web = wf_config.enrich_web.enable
    if generate_cover is None:
        generate_cover = wf_config.generate_cover.enable
    if transcribe is None:
        transcribe = wf_config.transcribe.enable

    generator_config = wf_config.podcast_generator
    if not isinstance(generator_config, PodcastGenerationConfig):
        raise ValueError(
            f"Preset '{preset_name}' has invalid or missing generator configuration"
        )

    importer_config = wf_config.importer
    if not isinstance(importer_config, ImporterConfig):
        raise ValueError(
            f"Preset '{preset_name}' has invalid or missing importer configuration"
        )

    transcriber_config = wf_config.transcribe.podcast_transcriber
    if transcribe and not isinstance(transcriber_config, PodcastTranscriptionConfig):
        raise ValueError(
            f"Preset '{preset_name}' has invalid or missing transcriber configuration"
        )

    transcription_langs = (
        transcriber_config.languages
        if isinstance(transcriber_config, PodcastTranscriptionConfig)
        else []
    )
    gen_languages = generator_config.languages
    gen_length = generator_config.length

    if not length:
        length = gen_length
    if not languages:
        languages = gen_languages
    if not languages:
        languages = ["en"]
    languages = [lang.lower() for lang in languages]

    if length and length != "auto":
        length = resolve_duration(length)

    # 1. Initialize Notebook
    notebook_info = await init_notebook_step(
        importer_config=importer_config,
        notebooklm_config=notebooklm_config,
        title=title,
        notebook_id=notebook_id,
        from_source=source_url,
    )

    derived_notebook_id = notebook_info["notebook_id"]
    derived_title = notebook_info.get("derived_title") or title or "Podcast"
    source_id = notebook_info.get("source_id")

    await DBOS.set_event_async("notebook_id", derived_notebook_id)
    await DBOS.set_event_async("notebook_title", derived_title)

    working_dir = str(get_workflow_dir(workdir, workflow_id))
    os.makedirs(working_dir, exist_ok=True)

    # 2. Start independent cover generation and enrichment.
    cover_task: Optional[asyncio.Task[str]] = None
    if generate_cover:
        await DBOS.set_event_async("current_step", "prepare_assets")
        cover_retry_count = wf_config.generate_cover.retry_count
        cover_task = asyncio.create_task(
            generate_cover_step(
                derived_notebook_id,
                working_dir,
                notebooklm_config,
                cover_spec=wf_config.generate_cover.spec,
                retry_count=cover_retry_count,
            )
        )

    enrichment_needed = enrich_web or length == "auto"
    enrichment_task: Optional[asyncio.Task[research.ResearchResult]] = None
    if enrichment_needed and source_id:
        await DBOS.set_event_async("current_step", "prepare_assets")
        enrichment_task = asyncio.create_task(
            enrich_source_step(
                derived_notebook_id,
                source_id,
                wf_config.enrich_web,
                notebooklm_config,
            )
        )

    async def cancel_background_tasks() -> None:
        tasks: list[asyncio.Future[Any]] = [
            task
            for task in (cover_task, enrichment_task)
            if task is not None and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    try:
        res_result: Optional[research.ResearchResult] = None
        if length == "auto" and enrichment_task is not None:
            res_result = await enrichment_task
            length = res_result.suggested_duration or "20 minutes"

        if not length or length == "auto":
            length = "20 minutes"

        logger.info(f"Auto-detected length: {length}")

        # 3. Audio Job Creation
        await DBOS.set_event_async("current_step", "create_audio_jobs")
        if not source_id:
            raise ValueError("source_id is required for podcast audio generation.")

        audio_tasks = await create_podcast_audio_jobs_step(
            derived_notebook_id,
            languages,
            length,
            source_id,
            generator_config,
            notebooklm_config,
        )

        # Audio tagging needs the cover, and the workflow must surface any
        # enrichment failure, but both operations have run alongside audio job
        # submission.
        cover_path = None
        if cover_task is not None:
            cover_path = await cover_task
            await DBOS.set_event_async("cover_path", cover_path)
        if enrichment_task is not None and res_result is None:
            await enrichment_task
    except BaseException:
        await cancel_background_tasks()
        raise

    # 4. Audio Job Polling & Processing
    await DBOS.set_event_async("current_step", "process_audio_tasks")
    transcribe_retry_count = wf_config.transcribe.retry_count
    transcription_cfg = (
        transcriber_config
        if isinstance(transcriber_config, PodcastTranscriptionConfig)
        else None
    )
    processed_artifacts = await asyncio.gather(
        *(
            process_single_audio_task_step(
                notebook_id=derived_notebook_id,
                notebook_title=derived_title,
                notebook_created_at=notebook_info.get("created_at"),
                task_info=task,
                cover_image_path=cover_path,
                working_dir=working_dir,
                transcribe=transcribe,
                transcription_languages=transcription_langs,
                transcribe_retry_count=transcribe_retry_count,
                transcription_config=transcription_cfg,
                tagging_config=wf_config.tagging,
                gcp_config=gcp_config,
                notebooklm_config=notebooklm_config,
            )
            for task in audio_tasks
        )
    )

    logger.info(
        f"Processed podcasts: {[art.path for art in processed_artifacts if art and art.path]}"
    )

    # 5. Distribution
    if wf_config.distribute:
        await DBOS.set_event_async("current_step", "distribute")
        logger.info(f"Distributing to {len(wf_config.distribute)} targets...")
        wf_meta = {
            "id": workflow_id,
            "source_url": source_url,
            "notebook": {
                "id": derived_notebook_id,
                "title": derived_title,
                "url": notebook_mod.get_notebook_url(derived_notebook_id),
                "creation_date": (notebook_info.get("created_at") or "")[:10],
            },
            "preset": preset_name,
            "artifacts": [
                {
                    "id": artifact.artifact_id,
                    "name": artifact.title,
                    "language": artifact.metadata.get("generate-podcast", {}).get(
                        "language"
                    ),
                    "path": artifact.path,
                    "lrc_path": artifact.lrc_path,
                }
                for artifact in processed_artifacts
                if artifact and artifact.path
            ],
        }
        targets: list[DistributionConfig] = []
        for target in wf_config.distribute:
            if not isinstance(target, DistributionConfig):
                raise ValueError(f"Invalid distribution configuration: {target}")
            targets.append(target)
        await asyncio.gather(
            *(
                distribute_step(target, working_dir, metadata=wf_meta)
                for target in targets
            )
        )

    logger.info("=== Workflow Complete ===")
    await DBOS.set_event_async("current_step", "completed")
    return {
        "workflow_id": workflow_id,
        "notebook_id": derived_notebook_id,
        "files": [a.path for a in processed_artifacts if a],
    }
