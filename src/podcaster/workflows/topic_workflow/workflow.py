"""Durable DBOS Workflow for topic-driven podcast generation."""

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
from podcaster.models import PodcastGenArtifact, PodcastGenTask
from podcaster.utils.duration import parse_duration_minutes, resolve_duration
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
    NotebookDistributionMetadata,
    TopicWorkflowNotification,
    WorkflowDistributionMetadata,
)
from .config import TopicWorkflowConfig
from .models import (
    TopicArticleDescriptor,
    TopicWorkflowRecipe,
)

logger = logging.getLogger(__name__)


# --- TOPIC DBOS STEPS ---


@DBOS.step()
async def init_topic_notebook_step(
    notebooklm_config: NotebookLMConfig,
    topic: str,
    title: str | None = None,
    notebook_id: str | None = None,
) -> dict:
    """DBOS Step to initialize a remote notebook with a topic seed source."""
    async with (
        log_task(
            "init_topic_notebook_step",
            logger,
            title=title,
            topic=topic[:50],
        ),
        get_notebooklm_client(notebooklm_config) as client,
    ):
        return await notebook_mod.init_topic_notebook(
            client=client,
            topic=topic,
            title=title,
            notebook_id=notebook_id,
        )


@DBOS.step()
async def enrich_topic_query_step(
    notebook_id: str,
    query: str,
    mode: str,
    enrich_config: EnrichWebConfig,
    notebooklm_config: NotebookLMConfig,
    source_id: str | None = None,
) -> research.ResearchResult:
    """DBOS Step to conduct web research directly from a topic query."""
    fallback_imp = (
        enrich_config.spec.fallback_importer
        if isinstance(enrich_config.spec.fallback_importer, ImporterConfig)
        else None
    )
    async with (
        log_task(
            "enrich_topic_query_step",
            logger,
            notebook_id=notebook_id,
            query=query[:50],
            mode=mode,
        ),
        get_notebooklm_client(notebooklm_config) as client,
    ):
        return await research.research_from_query(
            notebook_id=notebook_id,
            query=query,
            client=client,
            notebooklm_config=notebooklm_config,
            source_id=source_id,
            options=research.ResearchOptions(
                mode=mode,
                fallback_importer=fallback_imp,
                max_import_failures=enrich_config.spec.max_import_failures,
            ),
        )


@DBOS.step()
async def infer_topic_podcast_duration_step(
    notebook_id: str,
    topic: str,
    focus: str | None,
    notebooklm_config: NotebookLMConfig,
) -> str:
    """Query NotebookLM chat to infer podcast duration for auto length."""
    async with (
        log_task(
            "infer_topic_podcast_duration_step",
            logger,
            notebook_id=notebook_id,
            topic=topic[:50],
            focus=focus[:50] if focus else None,
        ),
        get_notebooklm_client(notebooklm_config) as client,
    ):
        focus_clause = f" with focus: {focus}" if focus else ""
        prompt = (
            f"Suggest an appropriate duration for a podcast about '{topic}'{focus_clause} "
            "based on the sources in this notebook.\n"
            "Reply with a plain duration string like '15 minutes' or '1 hour 5 minutes'. "
            "Typical range: 10–45 minutes.\n"
            "Do NOT include any citations, footnote markers, or explanations.\n"
            'Respond strictly in JSON format: {"suggested_duration": "..."}'
        )
        try:
            res = await client.chat.ask(notebook_id, prompt)
            answer_text = res.answer
            _, dur = research.parse_summary_response(answer_text)
            if dur and parse_duration_minutes(dur) is not None:
                return dur
        except Exception as e:
            logger.warning(f"Failed to infer podcast duration from NotebookLM: {e}")
        return "20 minutes"


@DBOS.step()
async def create_topic_podcast_audio_jobs_step(
    notebook_id: str,
    task_name: str,
    languages: list[str],
    length: str,
    format_args: dict,
    generator_config: PodcastGenerationConfig,
    notebooklm_config: NotebookLMConfig,
    track: int = 1,
) -> list[PodcastGenTask]:
    """DBOS Step to submit audio generation jobs for a topic generator item."""
    async with log_task(
        "create_topic_podcast_audio_jobs_step",
        logger,
        notebook_id=notebook_id,
        topic_task_name=task_name,
        languages=languages,
        length=length,
        track=track,
    ):
        tasks = []
        async for t in audio_gen_core.create_podcast_audio_jobs(
            notebook_id,
            task_name,
            languages,
            length,
            format_args,
            generator_config=generator_config,
            notebooklm_config=notebooklm_config,
            dry_run=False,
        ):
            t.metadata["track"] = track
            tasks.append(t)
        return tasks


# --- WORKFLOW ---


@dataclass
class TopicWorkflowOverrides:
    """CLI overrides for topic workflow."""

    enrich_web: bool | None = None
    generate_cover: bool | None = None
    transcribe: bool | None = None


@dataclass
class _TopicWorkflowSettings:
    generator_config: PodcastGenerationConfig
    transcriber_config: PodcastTranscriptionConfig | None
    enrich_web: bool
    generate_cover: bool
    transcribe: bool


def _resolve_topic_workflow_settings(
    wf_config: TopicWorkflowConfig,
    preset_name: str,
    overrides: TopicWorkflowOverrides,
) -> _TopicWorkflowSettings:
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

    transcriber_config = wf_config.transcribe.podcast_transcriber
    if transcribe and not isinstance(transcriber_config, PodcastTranscriptionConfig):
        raise ValueError(
            f"Preset '{preset_name}' has invalid or missing transcriber configuration"
        )

    return _TopicWorkflowSettings(
        generator_config=generator_config,
        transcriber_config=(
            transcriber_config
            if isinstance(transcriber_config, PodcastTranscriptionConfig)
            else None
        ),
        enrich_web=enrich_web,
        generate_cover=generate_cover,
        transcribe=transcribe,
    )


async def _create_all_topic_audio_jobs(
    recipe: TopicWorkflowRecipe,
    derived_notebook_id: str,
    generator_config: PodcastGenerationConfig,
    notebooklm_config: NotebookLMConfig,
    res_result: research.ResearchResult | None,
) -> list[PodcastGenTask]:
    all_audio_tasks: list[PodcastGenTask] = []
    total_podcasts = len(recipe.podcasts)
    for idx, descriptor in enumerate(recipe.podcasts, start=1):
        item_languages = descriptor.languages or generator_config.languages or ["en"]
        item_languages = [lang.lower() for lang in item_languages]

        item_length = descriptor.length or generator_config.length or "default"
        if item_length == "auto":
            if res_result and res_result.suggested_duration:
                item_length = res_result.suggested_duration
            else:
                item_length = await infer_topic_podcast_duration_step(
                    notebook_id=derived_notebook_id,
                    topic=recipe.research.query,
                    focus=descriptor.focus,
                    notebooklm_config=notebooklm_config,
                )
            logger.info(f"Auto-detected length for {descriptor.type}: {item_length}")
        item_length = resolve_duration(item_length)

        if descriptor.type == "TopicDebate":
            task_name = "topic-debate"
        elif descriptor.type == "TopicArticle":
            task_name = "main-article-with-author"
        else:
            task_name = "topic-deep-dive"

        format_args: dict[str, Any] = {"topic": recipe.research.query}
        if descriptor.focus:
            format_args["focus"] = descriptor.focus
        if descriptor.agenda:
            format_args["agenda"] = descriptor.agenda
        if descriptor.roles:
            format_args["roles"] = descriptor.roles

        if isinstance(descriptor, TopicArticleDescriptor) and descriptor.source_id:
            format_args["source_id"] = descriptor.source_id

        tasks = await create_topic_podcast_audio_jobs_step(
            notebook_id=derived_notebook_id,
            task_name=task_name,
            languages=item_languages,
            length=item_length,
            format_args=format_args,
            generator_config=generator_config,
            notebooklm_config=notebooklm_config,
            track=idx,
        )
        for t in tasks:
            t.metadata["track"] = idx
            t.metadata["total_tracks"] = total_podcasts
        all_audio_tasks.extend(tasks)
    return all_audio_tasks


def _build_topic_podcast_lines(
    descriptors: list[Any],
    processed_artifacts: list[PodcastGenArtifact],
) -> list[str]:
    lines: list[str] = []
    for idx, desc in enumerate(descriptors, start=1):
        track_artifacts = [
            art
            for art in processed_artifacts
            if art and art.metadata.get("track") == idx
        ]
        en_art = next(
            (
                art
                for art in track_artifacts
                if art.metadata.get("generate-podcast", {}).get("language") == "en"
            ),
            None,
        )
        title = (
            en_art.title
            if en_art
            else (
                track_artifacts[0].title if track_artifacts else desc.focus or desc.type
            )
        )
        track_langs = sorted(
            filter(
                None,
                (
                    art.metadata.get("generate-podcast", {}).get("language")
                    for art in track_artifacts
                ),
            )
        )
        lang_str = f" [{', '.join(track_langs)}]" if track_langs else ""
        lines.append(f"{idx}. {title} ({desc.type}){lang_str}")
    return lines


async def _distribute_topic_results(
    context: WorkflowNotebookContext,
    preset_name: str,
    workflow_id: str,
    recipe: TopicWorkflowRecipe,
    generator_config: PodcastGenerationConfig,
    processed_artifacts: list[PodcastGenArtifact],
    distribute_configs: list[Any],
) -> None:
    await DBOS.set_event_async("current_step", "distribute")
    logger.info(f"Distributing to {len(distribute_configs)} targets...")
    languages = sorted(
        filter(
            None,
            (
                artifact.metadata.get("generate-podcast", {}).get("language")
                for artifact in processed_artifacts
                if artifact
            ),
        )
    )
    if not languages:
        languages = generator_config.languages if generator_config.languages else ["en"]

    podcast_lines = _build_topic_podcast_lines(recipe.podcasts, processed_artifacts)

    notification = TopicWorkflowNotification(
        preset=preset_name,
        title=context.title,
        topic=recipe.research.query,
        notebook_url=notebook_mod.get_notebook_url(context.notebook_id),
        languages=languages,
        podcasts=podcast_lines,
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
async def topic_workflow(
    preset_name: str,
    wf_config: TopicWorkflowConfig,
    recipe: TopicWorkflowRecipe,
    env: WorkflowEnvironment,
    overrides: TopicWorkflowOverrides | None = None,
) -> dict:
    """Durable DBOS Workflow for topic-driven multi-podcast generation."""
    opts = overrides or TopicWorkflowOverrides()
    logger.info("Starting topic workflow %s", env.workflow_id)
    await DBOS.set_event_async("preset", preset_name)
    await DBOS.set_event_async("workflow_id", env.workflow_id)
    await DBOS.set_event_async("current_step", "init_notebook")
    if env.notebooklm_config is None:
        raise ValueError("notebooklm_config is required")

    settings = _resolve_topic_workflow_settings(wf_config, preset_name, opts)

    # 1. Initialize Notebook with seed topic brief
    notebook_info = await init_topic_notebook_step(
        notebooklm_config=env.notebooklm_config,
        topic=recipe.research.query,
        title=recipe.title,
    )

    derived_notebook_id = notebook_info["notebook_id"]
    derived_title = (
        notebook_info.get("derived_title") or recipe.title or "Topic Podcast"
    )
    source_id = notebook_info.get("source_id")

    await DBOS.set_event_async("notebook_id", derived_notebook_id)
    await DBOS.set_event_async("notebook_title", derived_title)

    working_dir = str(get_workflow_dir(env.workdir, env.workflow_id))
    os.makedirs(working_dir, exist_ok=True)

    # 2. Start independent cover generation and web research concurrently
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

    any_auto = any(d.length == "auto" for d in recipe.podcasts) or (
        settings.generator_config.length == "auto"
    )
    enrichment_needed = settings.enrich_web or any_auto
    enrichment_task: asyncio.Task[research.ResearchResult] | None = None
    if enrichment_needed:
        await DBOS.set_event_async("current_step", "prepare_assets")
        enrichment_task = asyncio.create_task(
            enrich_topic_query_step(
                notebook_id=derived_notebook_id,
                query=recipe.research.query,
                mode=recipe.research.mode,
                enrich_config=wf_config.enrich_web,
                notebooklm_config=env.notebooklm_config,
                source_id=source_id,
            )
        )

    try:
        res_result: research.ResearchResult | None = None
        if enrichment_task is not None:
            res_result = await enrichment_task

        # 3. Audio Job Creation across all descriptors in recipe
        await DBOS.set_event_async("current_step", "create_audio_jobs")
        all_audio_tasks = await _create_all_topic_audio_jobs(
            recipe=recipe,
            derived_notebook_id=derived_notebook_id,
            generator_config=settings.generator_config,
            notebooklm_config=env.notebooklm_config,
            res_result=res_result,
        )

        cover_path = None
        if cover_task is not None:
            cover_path = await cover_task
            await DBOS.set_event_async("cover_path", cover_path)

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
        audio_tasks=all_audio_tasks,
        options=audio_opts,
    )

    logger.info(
        f"Processed topic podcasts: {[art.path for art in processed_artifacts if art and art.path]}"
    )

    # 5. Distribution
    if wf_config.distribute:
        await _distribute_topic_results(
            context=context,
            preset_name=preset_name,
            workflow_id=env.workflow_id,
            recipe=recipe,
            generator_config=settings.generator_config,
            processed_artifacts=processed_artifacts,
            distribute_configs=wf_config.distribute,
        )

    logger.info("=== Topic Workflow Complete ===")
    await DBOS.set_event_async("current_step", "completed")
    return {
        "workflow_id": env.workflow_id,
        "notebook_id": derived_notebook_id,
        "files": [a.path for a in processed_artifacts if a],
    }
