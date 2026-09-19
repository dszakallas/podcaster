import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

from dbos import DBOS

from podcaster import notebook as notebook_mod
from podcaster import research
from podcaster.audio_gen import core as audio_gen_core
from podcaster.config import (
    EnrichWebConfig,
    ImporterConfig,
    NotebookLMConfig,
    PodcastGenerationConfig,
    PodcastTranscriptionConfig,
)
from podcaster.models import (
    PodcastGenArtifact,
    PodcastGenTask,
)
from podcaster.utils.duration import resolve_duration
from podcaster.utils.files import get_workflow_dir
from podcaster.utils.logging import log_task
from podcaster.utils.notebooklm import get_notebooklm_client

from ..common import (
    AudioProcessingOptions,
    WorkflowEnvironment,
    WorkflowNotebookContext,
    cancel_tasks,
    distribute_workflow_results,
    generate_cover_step,
    process_audio_tasks,
)
from ..notifications import (
    ArtifactDistributionMetadata,
    DeepDiveArticleNotification,
    NotebookDistributionMetadata,
    WorkflowDistributionMetadata,
)
from .config import DeepDiveArticleConfig

logger = logging.getLogger(__name__)


# --- DBOS STEPS ---


@DBOS.step()
async def init_notebook_step(
    importer_config: ImporterConfig,
    notebooklm_config: NotebookLMConfig,
    title: str | None = None,
    notebook_id: str | None = None,
    from_source: str | None = None,
) -> dict:
    """DBOS Step to initialize or fetch NotebookLM notebook."""
    async with (
        log_task(
            "init_notebook_step",
            logger,
            title=title,
            from_source=from_source,
        ),
        get_notebooklm_client(notebooklm_config) as client,
    ):
        return await notebook_mod.init_notebook(
            importer=importer_config,
            client=client,
            title=title,
            notebook_id=notebook_id,
            from_source=from_source,
        )


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
    async with (
        log_task(
            "enrich_source_step",
            logger,
            notebook_id=notebook_id,
            source_id=source_id,
            mode=enrich_config.spec.mode,
        ),
        get_notebooklm_client(notebooklm_config) as client,
    ):
        return await research.research_from_source(
            notebook_id,
            source_id,
            client=client,
            notebooklm_config=notebooklm_config,
            options=research.ResearchOptions(
                mode=enrich_config.spec.mode,
                fallback_importer=fallback_imp,
                max_import_failures=enrich_config.spec.max_import_failures,
            ),
        )


@DBOS.step()
async def create_podcast_audio_jobs_step(
    notebook_id: str,
    languages: list[str],
    length: str,
    source_id: str,
    generator_config: PodcastGenerationConfig,
    notebooklm_config: NotebookLMConfig,
    track: int = 1,
) -> list[PodcastGenTask]:
    """DBOS Step to submit audio generation jobs."""
    async with log_task(
        "create_podcast_audio_jobs_step",
        logger,
        notebook_id=notebook_id,
        languages=languages,
        length=length,
        track=track,
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
            t.metadata["track"] = track
            t.metadata["total_tracks"] = 1
            tasks.append(t)
        return tasks


@dataclass
class DeepDiveArticleOverrides:
    """CLI overrides and source inputs for deep dive article workflow."""

    title: str | None = None
    source_url: str | None = None
    notebook_id: str | None = None
    length: str | None = None
    languages: list[str] | None = None
    enrich_web: bool | None = None
    generate_cover: bool | None = None
    transcribe: bool | None = None


@dataclass
class _ArticleWorkflowSettings:
    generator_config: PodcastGenerationConfig
    importer_config: ImporterConfig
    transcriber_config: PodcastTranscriptionConfig | None
    languages: list[str]
    length: str | None
    enrich_web: bool
    generate_cover: bool
    transcribe: bool


def _resolve_article_workflow_settings(
    wf_config: DeepDiveArticleConfig,
    preset_name: str,
    overrides: DeepDiveArticleOverrides,
) -> _ArticleWorkflowSettings:
    enrich_web = (
        overrides.enrich_web
        if overrides.enrich_web is not None
        else wf_config.enrich_web.enable
    )
    generate_cover = (
        overrides.generate_cover
        if overrides.generate_cover is not None
        else wf_config.generate_cover.enable
    )
    transcribe = (
        overrides.transcribe
        if overrides.transcribe is not None
        else wf_config.transcribe.enable
    )

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

    languages = overrides.languages or generator_config.languages or ["en"]
    languages = [lang.lower() for lang in languages]

    length = overrides.length or generator_config.length
    if length and length != "auto":
        length = resolve_duration(length)

    return _ArticleWorkflowSettings(
        generator_config=generator_config,
        importer_config=importer_config,
        transcriber_config=(
            transcriber_config
            if isinstance(transcriber_config, PodcastTranscriptionConfig)
            else None
        ),
        languages=languages,
        length=length,
        enrich_web=enrich_web,
        generate_cover=generate_cover,
        transcribe=transcribe,
    )


async def _distribute_article_results(
    context: WorkflowNotebookContext,
    preset_name: str,
    workflow_id: str,
    generator_config: PodcastGenerationConfig,
    processed_artifacts: list[PodcastGenArtifact],
    distribute_configs: list[Any],
    source_url: str | None = None,
) -> None:
    await DBOS.set_event_async("current_step", "distribute")
    logger.info(f"Distributing to {len(distribute_configs)} targets...")
    languages = (
        generator_config.languages
        if isinstance(generator_config, PodcastGenerationConfig)
        and generator_config.languages
        else ["en"]
    )
    notification = DeepDiveArticleNotification(
        preset=preset_name,
        title=context.title,
        notebook_url=notebook_mod.get_notebook_url(context.notebook_id),
        source_url=source_url,
        languages=languages,
        episodes=[
            f"{artifact.title} ({artifact.metadata.get('generate-podcast', {}).get('language', 'en')})"
            for artifact in processed_artifacts
            if artifact and artifact.path
        ],
    )
    dist_meta = WorkflowDistributionMetadata(
        id=workflow_id,
        preset=preset_name,
        notebook=NotebookDistributionMetadata(
            id=context.notebook_id,
            title=context.title,
            url=notebook_mod.get_notebook_url(context.notebook_id),
            creation_date=(context.created_at or "")[:10],
        ),
        artifacts=[
            ArtifactDistributionMetadata(
                id=artifact.artifact_id,
                name=artifact.title,
                language=artifact.metadata.get("generate-podcast", {}).get("language"),
                path=artifact.path,
                lrc_path=artifact.lrc_path,
            )
            for artifact in processed_artifacts
            if artifact and artifact.path
        ],
        notification=notification,
    )
    await distribute_workflow_results(
        working_dir=context.working_dir,
        dist_meta=dist_meta,
        distribute_configs=distribute_configs,
    )


@DBOS.workflow()
async def deep_dive_article_workflow(
    preset_name: str,
    wf_config: DeepDiveArticleConfig,
    env: WorkflowEnvironment,
    overrides: DeepDiveArticleOverrides | None = None,
) -> dict:
    """Durable DBOS Workflow for Deep Dive Article podcast generation."""
    opts = overrides or DeepDiveArticleOverrides()
    logger.info("Starting workflow %s", env.workflow_id)
    await DBOS.set_event_async("preset", preset_name)
    await DBOS.set_event_async("workflow_id", env.workflow_id)
    await DBOS.set_event_async("current_step", "init_notebook")
    if env.notebooklm_config is None:
        raise ValueError("notebooklm_config is required")

    settings = _resolve_article_workflow_settings(wf_config, preset_name, opts)

    # 1. Initialize Notebook
    notebook_info = await init_notebook_step(
        importer_config=settings.importer_config,
        notebooklm_config=env.notebooklm_config,
        title=opts.title,
        notebook_id=opts.notebook_id,
        from_source=opts.source_url,
    )

    derived_notebook_id = notebook_info["notebook_id"]
    derived_title = notebook_info.get("derived_title") or opts.title or "Podcast"
    source_id = notebook_info.get("source_id")

    await DBOS.set_event_async("notebook_id", derived_notebook_id)
    await DBOS.set_event_async("notebook_title", derived_title)

    working_dir = str(get_workflow_dir(env.workdir, env.workflow_id))
    os.makedirs(working_dir, exist_ok=True)

    # 2. Start independent cover generation and enrichment.
    cover_task: asyncio.Task[str] | None = None
    if settings.generate_cover:
        await DBOS.set_event_async("current_step", "prepare_assets")
        cover_task = asyncio.create_task(
            generate_cover_step(
                derived_notebook_id,
                working_dir,
                env.notebooklm_config,
                cover_spec=wf_config.generate_cover.spec,
                retry_count=wf_config.generate_cover.retry_count,
            )
        )

    length = settings.length
    enrichment_needed = settings.enrich_web or length == "auto"
    enrichment_task: asyncio.Task[research.ResearchResult] | None = None
    if enrichment_needed and source_id:
        await DBOS.set_event_async("current_step", "prepare_assets")
        enrichment_task = asyncio.create_task(
            enrich_source_step(
                derived_notebook_id,
                source_id,
                wf_config.enrich_web,
                env.notebooklm_config,
            )
        )

    try:
        res_result: research.ResearchResult | None = None
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
            settings.languages,
            length,
            source_id,
            settings.generator_config,
            env.notebooklm_config,
        )
        for t in audio_tasks:
            t.metadata["track"] = 1
            t.metadata["total_tracks"] = 1

        cover_path = None
        if cover_task is not None:
            cover_path = await cover_task
            await DBOS.set_event_async("cover_path", cover_path)
        if enrichment_task is not None and res_result is None:
            await enrichment_task
    except BaseException:
        await cancel_tasks(cover_task, enrichment_task)
        raise

    # 4. Audio Job Polling & Processing
    await DBOS.set_event_async("current_step", "process_audio_tasks")
    context = WorkflowNotebookContext(
        notebook_id=derived_notebook_id,
        title=derived_title,
        created_at=notebook_info.get("created_at"),
        working_dir=working_dir,
    )
    audio_opts = AudioProcessingOptions(
        notebooklm_config=env.notebooklm_config,
        cover_image_path=cover_path,
        tagging_config=wf_config.tagging,
        transcribe=settings.transcribe,
        transcription_languages=(
            settings.transcriber_config.languages if settings.transcriber_config else []
        ),
        transcribe_retry_count=wf_config.transcribe.retry_count,
        transcription_config=settings.transcriber_config,
        gcp_config=env.gcp_config,
    )
    processed_artifacts = await process_audio_tasks(
        context=context,
        audio_tasks=audio_tasks,
        options=audio_opts,
    )

    logger.info(
        f"Processed podcasts: {[art.path for art in processed_artifacts if art and art.path]}"
    )

    # 5. Distribution
    if wf_config.distribute:
        await _distribute_article_results(
            context=context,
            preset_name=preset_name,
            workflow_id=env.workflow_id,
            generator_config=settings.generator_config,
            processed_artifacts=processed_artifacts,
            distribute_configs=wf_config.distribute,
            source_url=opts.source_url,
        )

    logger.info("=== Workflow Complete ===")
    await DBOS.set_event_async("current_step", "completed")
    return {
        "workflow_id": env.workflow_id,
        "notebook_id": derived_notebook_id,
        "files": [a.path for a in processed_artifacts if a],
    }
