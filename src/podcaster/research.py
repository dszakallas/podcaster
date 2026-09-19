"""NotebookLM research orchestration and enrichment."""

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import AsyncGenerator, AsyncIterable, Callable
from dataclasses import dataclass
from typing import Any

from notebooklm.types import ResearchSource
from notebooklm.types import ResearchTask as NlmResearchTask

from .config import ImporterConfig, NotebookLMConfig
from .importers import (
    DEFAULT_IMPORTER_KEY,
    ChainImporter,
    Importer,
    NativeImporter,
    ScraperImporter,
    build_importer,
    evaluate_importer_match,
    execute_importer,
    extract_drive_file_id,
    import_source,
    normalize_source,
)
from .models import ResearchResult, ResearchTask, TaskStatus
from .scrapers import (
    scrape,
    scrape_source,
)
from .utils import async_iter
from .utils.duration import parse_duration_minutes
from .utils.notebooklm import RetryingNotebookLMClient, get_notebooklm_client
from .utils.retry import is_transient_network_exception

logger = logging.getLogger(__name__)

DEFAULT_RESEARCH_TOPIC = "general"
DEFAULT_RESEARCH_DURATION = "20 minutes"

RESEARCH_SUMMARY_PROMPT = (
    "1. Summarize this source in exactly one sentence with dates for important events.\n"
    "2. Suggest a duration for a deep-dive podcast about this article. "
    "Reply with a plain duration string like '15 minutes' or '1 hour 5 minutes'. "
    "Typical range: 10–45 minutes.\n"
    "Do NOT include any citations, footnote markers (such as [1], [2]), or source references anywhere in your response.\n"
    'Respond strictly in JSON format: {"summary": "...", "suggested_duration": "..."}'
)


def strip_citations(text: str) -> str:
    """Strips inline citation markers (e.g., [1], [1, 2], [1-3]) and trailing citation blocks from text."""
    lines = text.splitlines()
    cleaned_lines = []
    for line in lines:
        stripped_line = line.strip()
        if re.match(
            r"^(?:Sources?|Citations?|References?|\[\d+\](?:\s|:|$))",
            stripped_line,
            re.IGNORECASE,
        ):
            break
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    # Remove inline citation markers like [1], [1, 2], [1-3]
    text = re.sub(r"\s*\[\d+(?:\s*[,-\u2013\u2014]\s*\d+)*\]", "", text)
    # Clean up multiple whitespace characters within lines
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def parse_summary_response(answer_text: str) -> tuple[str, str]:
    """Parses the summary and suggested duration from NotebookLM chat response,
    robustly handling markdown wrappers, citations, and invalid JSON constructs.
    """
    raw_text = answer_text.strip()

    # Remove markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw_text, re.DOTALL)
    text_to_parse = fence_match.group(1).strip() if fence_match else raw_text

    data = None

    # Attempt 1: parse as-is from first '{' using JSONDecoder.raw_decode
    start_idx = text_to_parse.find("{")
    if start_idx != -1:
        decoder = json.JSONDecoder()
        with contextlib.suppress(Exception):
            data, _ = decoder.raw_decode(text_to_parse, start_idx)

        # Attempt 2: if attempt 1 failed, strip citation markers from text_to_parse and retry
        if not isinstance(data, dict):
            cleaned_text = strip_citations(text_to_parse)
            start_idx = cleaned_text.find("{")
            if start_idx != -1:
                with contextlib.suppress(Exception):
                    data, _ = decoder.raw_decode(cleaned_text, start_idx)

    if isinstance(data, dict):
        summary = str(data.get("summary", ""))
        suggested_duration = str(data.get("suggested_duration", ""))

        summary = strip_citations(summary)
        suggested_duration = strip_citations(suggested_duration)

        if summary or suggested_duration:
            return summary, suggested_duration

    # Fallback if no valid JSON dict could be extracted or summary was empty
    cleaned_fallback = strip_citations(raw_text)
    return cleaned_fallback, ""


def _coerce_poll_result(res: Any) -> NlmResearchTask:
    """Coerce NotebookLM poll result to NlmResearchTask model at boundary."""
    if isinstance(res, NlmResearchTask):
        return res
    if isinstance(res, dict):
        return NlmResearchTask(
            task_id=res.get("task_id", ""),
            status=res.get("status", ""),
            sources=tuple(res.get("sources", ())),
            query=res.get("query", ""),
            summary=res.get("summary", ""),
        )
    return res


async def create_research_job(
    notebook_id: str,
    source_id: str,
    client: RetryingNotebookLMClient,
    mode: str = "fast",
) -> ResearchTask:
    """Create a research job using an already-open NotebookLM client."""
    logger.debug(f"Fetching guide for source {source_id}...")
    guide = await client.sources.get_guide(notebook_id, source_id)
    keywords = list(guide.keywords) if guide.keywords else []
    if not keywords:
        logger.debug(f"No keywords found in guide for source {source_id}.")
        topic = DEFAULT_RESEARCH_TOPIC
    else:
        topic = ", ".join(keywords)

    logger.debug(
        f"Generating summary and duration suggestion for source {source_id}..."
    )
    summary_res = await client.chat.ask(
        notebook_id, RESEARCH_SUMMARY_PROMPT, source_ids=[source_id]
    )

    answer_text = summary_res.answer
    summary, suggested_duration = parse_summary_response(answer_text)

    if not suggested_duration or parse_duration_minutes(suggested_duration) is None:
        logger.warning(
            f"Could not parse suggested duration {suggested_duration!r}, defaulting to {DEFAULT_RESEARCH_DURATION}."
        )
        suggested_duration = DEFAULT_RESEARCH_DURATION

    prompt = f"Topic: {topic}. Context: {summary}"
    logger.debug(f"Starting research with prompt: {prompt} (mode: {mode})")
    job = await client.research.start(notebook_id, prompt, mode=mode)
    if not job:
        raise RuntimeError("Failed to start research job.")

    task_id = job.task_id or ""
    return ResearchTask(
        notebook_id=notebook_id,
        source_id=source_id,
        task_id=task_id,
        topic=topic,
        summary=summary,
        suggested_duration=suggested_duration,
    )


@dataclass
class ResearchOptions:
    """Options for research execution, fallback importing, and callbacks."""

    mode: str = "fast"
    task: ResearchTask | None = None
    fallback_importer: ImporterConfig | None = None
    max_import_failures: int | None = None
    on_start_callback: Callable[[str, str, str, str], Any] | None = None


async def _poll_single_research_task(
    client: RetryingNotebookLMClient,
    notebook_id: str,
    task_id: str,
) -> tuple[list[ResearchSource], bool, str | None, NlmResearchTask | None]:
    started_at = time.time()
    found_sources: list[ResearchSource] = []
    poll_failed = False
    error_msg: str | None = None
    poll_res: NlmResearchTask | None = None

    while True:
        try:
            poll_res = _coerce_poll_result(await client.research.poll(notebook_id))
            status = poll_res.status
            logger.debug(f"Research status: {status}")

            sources_list_raw = poll_res.sources
            if status == TaskStatus.COMPLETED or (
                status == TaskStatus.IN_PROGRESS and len(sources_list_raw) > 0
            ):
                found_sources = list(sources_list_raw)
                break
            elif status == TaskStatus.FAILED:
                poll_failed = True
                error_msg = f"Research job failed: {poll_res}"
                break
            elif status == "no_research":
                logger.warning(
                    "No active research job found in polling list. Exiting poll loop."
                )
                found_sources = []
                break
        except Exception as e:
            if not is_transient_network_exception(e):
                logger.error(
                    f"Non-retryable error polling research job for notebook {notebook_id}: {e}"
                )
                poll_failed = True
                error_msg = str(e)
                break
            logger.warning(
                f"Transient network error polling research for notebook {notebook_id}: {e}"
            )

        if time.time() - started_at > 600.0:
            poll_failed = True
            error_msg = (
                f"Research polling timed out after 600s for notebook {notebook_id}"
            )
            break

        await asyncio.sleep(5)

    return found_sources, poll_failed, error_msg, poll_res


async def _cleanup_errored_sources(
    client: RetryingNotebookLMClient, notebook_id: str
) -> None:
    try:
        sources_list = await client.sources.list(notebook_id)
        for existing_src in sources_list or []:
            if existing_src.status == "error":
                logger.info(
                    f"Removing errored source from NotebookLM: {existing_src.id}"
                )
                await client.sources.delete(notebook_id, existing_src.id)
    except Exception as delete_err:
        logger.debug(f"Error cleaning up errored sources: {delete_err}")


async def _import_missing_after_batch(
    client: RetryingNotebookLMClient,
    notebook_id: str,
    sources: list[ResearchSource],
    fallback_importer: ImporterConfig | None,
    imported: list[dict[str, Any]],
) -> int:
    try:
        current_sources = await client.sources.list(notebook_id)
        existing_urls = {
            s.url.strip().lower() for s in (current_sources or []) if s.url
        }
    except Exception:
        existing_urls = set()

    unimported = [
        s for s in sources if s.url and s.url.strip().lower() not in existing_urls
    ]
    failed_count = 0
    for missing_src in unimported:
        src_url = missing_src.url
        src_title = missing_src.title
        fallback_success = False
        if fallback_importer is not None and src_url:
            try:
                imp_res = await import_source(
                    notebook_id,
                    src_url,
                    importer=fallback_importer,
                    client=client,
                    title=src_title,
                )
                if imp_res.get("source_id"):
                    imported.append({"id": imp_res["source_id"], "url": src_url})
                    fallback_success = True
            except Exception as fe:
                logger.warning(f"Fallback import exception for '{src_url}': {fe}")
        if not fallback_success:
            failed_count += 1
    return failed_count


async def _import_sources_individually(
    client: RetryingNotebookLMClient,
    notebook_id: str,
    effective_task_id: str,
    sources: list[ResearchSource],
    fallback_importer: ImporterConfig | None,
    imported: list[dict[str, Any]],
) -> int:
    failed_count = 0
    for src in sources:
        src_url = src.url
        src_title = src.title
        src_task_id = getattr(src, "research_task_id", None) or effective_task_id
        import_success = False
        try:
            single_imported = await client.research.import_sources(
                notebook_id, src_task_id, [src]
            )
            if single_imported:
                imported.extend(single_imported)
                import_success = True
        except Exception as e:
            logger.warning(
                f"NotebookLM import failed for research source '{src_url or src_title}': {e}"
            )

        if not import_success and fallback_importer is not None and src_url:
            try:
                imp_res = await import_source(
                    notebook_id,
                    src_url,
                    importer=fallback_importer,
                    client=client,
                    title=src_title,
                )
                if imp_res.get("source_id"):
                    imported.append({"id": imp_res["source_id"], "url": src_url})
                    import_success = True
                else:
                    logger.warning(
                        f"Fallback import failed for '{src_url}': {imp_res.get('error')}"
                    )
            except Exception as fe:
                logger.warning(f"Fallback import exception for '{src_url}': {fe}")

        if not import_success:
            failed_count += 1
    return failed_count


async def _import_research_sources(
    client: RetryingNotebookLMClient,
    notebook_id: str,
    effective_task_id: str,
    sources: list[ResearchSource],
    fallback_importer: ImporterConfig | None,
    max_import_failures: int | None,
) -> list[dict[str, Any]]:
    if not sources:
        return []

    logger.info(
        f"Importing {len(sources)} research sources (task_id: {effective_task_id})..."
    )
    imported: list[dict[str, Any]] = []
    failed_count = 0

    batch_success = False
    try:
        if hasattr(client.research, "import_sources_with_verification"):
            batch_res = await client.research.import_sources_with_verification(
                notebook_id, effective_task_id, sources
            )
        else:
            batch_res = await client.research.import_sources(
                notebook_id, effective_task_id, sources
            )
        if batch_res:
            imported.extend(batch_res)
        batch_success = True
        logger.info(
            f"Successfully imported {len(batch_res or [])} sources via batch import."
        )
    except Exception as batch_err:
        logger.warning(
            f"Batch import failed: {batch_err}. Attempting individual source imports..."
        )

    if batch_success:
        failed_count += await _import_missing_after_batch(
            client, notebook_id, sources, fallback_importer, imported
        )
    else:
        failed_count += await _import_sources_individually(
            client, notebook_id, effective_task_id, sources, fallback_importer, imported
        )

    if max_import_failures is not None and failed_count > max_import_failures:
        raise RuntimeError(
            f"Research import failed: {failed_count} failed import(s) exceeded max_import_failures limit ({max_import_failures})."
        )

    if failed_count > 0:
        await _cleanup_errored_sources(client, notebook_id)

    return imported


async def poll_research_jobs(
    tasks: AsyncIterable[ResearchTask],
    notebooklm_config: NotebookLMConfig,
    fallback_importer: ImporterConfig | None = None,
    max_import_failures: int | None = None,
) -> AsyncGenerator[ResearchResult, None]:
    """Poll research jobs for completion and import discovered sources.

    Yields a ResearchResult per task. If a task fails or times out, its status is set
    to FAILED with error details, allowing other tasks in the stream to continue.
    """
    async with get_notebooklm_client(notebooklm_config) as client:
        async for t in tasks:
            found_sources, poll_failed, error_msg, poll_res = (
                await _poll_single_research_task(client, t.notebook_id, t.task_id)
            )

            if poll_failed:
                yield ResearchResult(
                    notebook_id=t.notebook_id,
                    source_id=t.source_id,
                    task_id=t.task_id,
                    topic=t.topic,
                    summary=t.summary,
                    suggested_duration=t.suggested_duration,
                    status=TaskStatus.FAILED,
                    error=error_msg,
                )
                continue

            logger.debug(f"Found {len(found_sources)} sources.")

            effective_task_id = (
                (poll_res.task_id if poll_res else None)
                or (
                    getattr(found_sources[0], "research_task_id", None)
                    if found_sources
                    else None
                )
                or t.task_id
            )

            imported = await _import_research_sources(
                client=client,
                notebook_id=t.notebook_id,
                effective_task_id=effective_task_id,
                sources=found_sources,
                fallback_importer=fallback_importer,
                max_import_failures=max_import_failures,
            )

            yield ResearchResult(
                notebook_id=t.notebook_id,
                source_id=t.source_id,
                task_id=effective_task_id,
                topic=t.topic,
                summary=t.summary,
                suggested_duration=t.suggested_duration,
                status=TaskStatus.COMPLETED,
                found_count=len(found_sources),
                imported_count=len(imported),
                imported=imported,
            )


async def research_from_source(
    notebook_id: str,
    source_id: str,
    client: RetryingNotebookLMClient,
    notebooklm_config: NotebookLMConfig,
    options: ResearchOptions | None = None,
) -> ResearchResult:
    """Run research for a notebook source and poll for results."""
    opts = options or ResearchOptions()
    if not opts.task:
        task = await create_research_job(
            notebook_id, source_id, client=client, mode=opts.mode
        )
        if opts.on_start_callback:
            cb_res = opts.on_start_callback(
                task.task_id, task.topic, task.summary, task.suggested_duration
            )
            if asyncio.iscoroutine(cb_res) or hasattr(cb_res, "__await__"):
                await cb_res
        task_obj = task
    else:
        task_obj = opts.task

    res: ResearchResult | None = None
    async for r in poll_research_jobs(
        async_iter(task_obj),
        notebooklm_config,
        fallback_importer=opts.fallback_importer,
        max_import_failures=opts.max_import_failures,
    ):
        res = r
    if res is None:
        raise RuntimeError("Research polling failed to return a result.")
    if res.status == TaskStatus.FAILED:
        raise RuntimeError(res.error or "Research job failed.")
    return res


async def create_topic_research_job(
    notebook_id: str,
    query: str,
    client: RetryingNotebookLMClient,
    mode: str = "fast",
    source_id: str | None = None,
) -> ResearchTask:
    """Create a research job from a direct query using an already-open NotebookLM client."""
    logger.debug(f"Starting topic research with query: {query} (mode: {mode})")
    job = await client.research.start(notebook_id, query, mode=mode)
    if not job:
        raise RuntimeError("Failed to start research job.")

    task_id = job.task_id or ""

    summary = query
    suggested_duration = DEFAULT_RESEARCH_DURATION
    try:
        summary_res = await client.chat.ask(
            notebook_id,
            RESEARCH_SUMMARY_PROMPT,
            source_ids=[source_id] if source_id else None,
        )
        answer_text = summary_res.answer
        sum_text, dur_text = parse_summary_response(answer_text)
        if sum_text:
            summary = sum_text
        if dur_text and parse_duration_minutes(dur_text) is not None:
            suggested_duration = dur_text
    except Exception as e:
        logger.warning(
            f"Could not infer summary/duration from NotebookLM, defaulting: {e}"
        )

    return ResearchTask(
        notebook_id=notebook_id,
        source_id=source_id or "",
        task_id=task_id,
        topic=query,
        summary=summary,
        suggested_duration=suggested_duration,
    )


async def research_from_query(
    notebook_id: str,
    query: str,
    client: RetryingNotebookLMClient,
    notebooklm_config: NotebookLMConfig,
    source_id: str | None = None,
    options: ResearchOptions | None = None,
) -> ResearchResult:
    """Run research for a topic query and import discovered sources."""
    opts = options or ResearchOptions()
    if not opts.task:
        task = await create_topic_research_job(
            notebook_id, query, client=client, mode=opts.mode, source_id=source_id
        )
        if opts.on_start_callback:
            cb_res = opts.on_start_callback(
                task.task_id, task.topic, task.summary, task.suggested_duration
            )
            if asyncio.iscoroutine(cb_res) or hasattr(cb_res, "__await__"):
                await cb_res
        task_obj = task
    else:
        task_obj = opts.task

    res: ResearchResult | None = None
    async for r in poll_research_jobs(
        async_iter(task_obj),
        notebooklm_config,
        fallback_importer=opts.fallback_importer,
        max_import_failures=opts.max_import_failures,
    ):
        res = r
    if res is None:
        raise RuntimeError("Research polling failed to return a result.")
    if res.status == TaskStatus.FAILED:
        raise RuntimeError(res.error or "Research job failed.")
    return res


__all__ = [
    "DEFAULT_IMPORTER_KEY",
    "DEFAULT_RESEARCH_DURATION",
    "DEFAULT_RESEARCH_TOPIC",
    "RESEARCH_SUMMARY_PROMPT",
    "ChainImporter",
    "Importer",
    "NativeImporter",
    "ResearchOptions",
    "ScraperImporter",
    "build_importer",
    "create_research_job",
    "create_topic_research_job",
    "evaluate_importer_match",
    "execute_importer",
    "extract_drive_file_id",
    "import_source",
    "normalize_source",
    "parse_summary_response",
    "poll_research_jobs",
    "research_from_query",
    "research_from_source",
    "scrape",
    "scrape_source",
    "strip_citations",
]
