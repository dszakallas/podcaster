import asyncio
import json
import logging
import os
import re
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, AsyncGenerator, AsyncIterable, Callable, List, Optional

from notebooklm.types import ResearchSource

from .config import ImporterConfig, NotebookLMConfig, ScraperConfig
from .models import ResearchResult, ResearchTask, TaskStatus
from .utils.duration import parse_duration_minutes
from .utils.files import sanitize
from .utils.notebooklm import RetryingNotebookLMClient, get_notebooklm_client
from .utils.retry import is_transient_network_exception

logger = logging.getLogger(__name__)

DEFAULT_SCRAPER_TOOL = "playwright"
DEFAULT_IMPORTER_KEY = "default"
DEFAULT_RESEARCH_TOPIC = "general"
DEFAULT_RESEARCH_DURATION = "20 minutes"

SCRAPER_PROMPT_TEMPLATE = (
    'Use the {{ tool }} MCP tool to navigate to the URL "{{ url }}". '
    "If you encounter bot detection, cookie consent banners, paywalls, or blank pages, attempt to bypass them. "
    "Useful techniques include: waiting for elements, dismissing cookie/consent popups, scrolling the page "
    "naturally, and emulating a common device viewport or user agent if supported by the tool. "
    "If the article is locked behind a paywall (such as if the article text does not show at all, is abruptly ending in the middle, "
    "or displays a callout to login/register to view the full content), bail out immediately and return a JSON object with an error key. "
    "Extract the article content, title, author, and creation date. "
    "In addition, find all hyperlinks within the article body that are "
    "useful for content enrichment. Extract 3-5 keywords summarizing the article's topics, and find "
    "the publication date. Close the browser tab when finished. "
    "Finally, respond with a single, valid NDJSON object on a single line without whitespace in this exact "
    "emulating what a real user would do as an anti-bot evasion measure. Make sure that the browser is closed in the end. "
    "IMPORTANT OUTPUT REQUIREMENTS, YOU MUST ADHERE: "
    "On success, respond with a single, valid NDJSON object on a single line without whitespace on this exact "
    'format: {"url":"...","title":"...","author":"...","created_at":"<date>","content":"<extracted content>","links":[...],"keywords":[...]}. '
    "On failure respond with with a single, valid NDJSON object on a single line without whitespace on this exact "
    'format: {"error":"<error description>"}. '
    "Your response must be a valid NDJSON at all times, DO NOT INCLUDE ANY OTHER TEXT, MARKDOWN, ETC IN YOUR RESPONSE."
)

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
        try:
            data, _ = decoder.raw_decode(text_to_parse, start_idx)
        except Exception:
            pass

        # Attempt 2: if attempt 1 failed, strip citation markers from text_to_parse and retry
        if not isinstance(data, dict):
            cleaned_text = strip_citations(text_to_parse)
            start_idx = cleaned_text.find("{")
            if start_idx != -1:
                try:
                    data, _ = decoder.raw_decode(cleaned_text, start_idx)
                except Exception:
                    pass

    if isinstance(data, dict):
        summary = str(data.get("summary", ""))
        suggested_duration = str(data.get("suggested_duration", ""))

        summary = strip_citations(summary)
        suggested_duration = strip_citations(suggested_duration)

        if summary:
            return summary, suggested_duration

    # Fallback if no valid JSON dict could be extracted or summary was empty
    cleaned_fallback = strip_citations(raw_text)
    return cleaned_fallback, ""


def normalize_source(source: str) -> str:
    """Normalizes source strings so local file paths become file:// absolute URLs."""
    if source.startswith(("http://", "https://", "gdrive:")):
        return source

    if source.startswith("file://"):
        file_path = Path(source[7:]).resolve()
        return file_path.as_uri()

    p = Path(source)
    if p.exists() or not source.startswith(
        ("http://", "https://", "gdrive://", "file://")
    ):
        try:
            return p.resolve().as_uri()
        except Exception:
            return source

    return source


class Importer(ABC):
    """Abstract interface for all importers."""

    def __init__(self, match_expressions: Optional[List[str]] = None):
        self.match_expressions = match_expressions or [".*"]

    def matches(self, source: str) -> bool:
        """Evaluates whether the source string matches this importer's criteria."""
        source = normalize_source(source)
        return evaluate_importer_match(self.match_expressions, source)

    @abstractmethod
    async def execute(
        self,
        notebook_id: str,
        source: str,
        client: RetryingNotebookLMClient,
        title: Optional[str] = None,
    ) -> dict:
        """Executes the import operation on a given source string."""
        ...


class NativeImporter(Importer):
    """Native importer utilizing NotebookLM's built-in file/URL/Drive importer."""

    def __init__(
        self,
        config: Optional[Any] = None,
        match_expressions: Optional[List[str]] = None,
    ):
        super().__init__(match_expressions=match_expressions)
        self.config = config

    async def execute(
        self,
        notebook_id: str,
        source: str,
        client: RetryingNotebookLMClient,
        title: Optional[str] = None,
    ) -> dict:
        if not self.matches(source):
            return {
                "source_id": None,
                "error": f"Source '{source}' did not match native importer criteria.",
            }
        try:
            src_id = await _import_native(
                notebook_id, source, title=title, client=client
            )
            return {"source_id": src_id, "importer": "native"}
        except Exception as e:
            return {"source_id": None, "error": f"[native]: {e}"}


class ScraperImporter(Importer):
    """Web scraper importer using agent-driven web scraping."""

    def __init__(
        self,
        config: Optional[Any] = None,
        match_expressions: Optional[List[str]] = None,
    ):
        super().__init__(match_expressions=match_expressions)
        self.config = config

    async def execute(
        self,
        notebook_id: str,
        source: str,
        client: RetryingNotebookLMClient,
        title: Optional[str] = None,
    ) -> dict:
        if not self.matches(source):
            return {
                "source_id": None,
                "error": f"Source '{source}' did not match scraper importer criteria.",
            }
        try:
            src_id = await _import_scraper(
                notebook_id,
                source,
                scraper_config=self.config,
                title=title,
                client=client,
            )
            return {"source_id": src_id, "importer": "scraper"}
        except Exception as e:
            return {"source_id": None, "error": f"[scraper]: {e}"}


class ChainImporter(Importer):
    """Composite importer executing a chain of sub-importers in priority order."""

    def __init__(
        self,
        importers: Optional[List[Importer]] = None,
        match_expressions: Optional[List[str]] = None,
    ):
        super().__init__(match_expressions=match_expressions)
        self.importers = importers or []

    async def execute(
        self,
        notebook_id: str,
        source: str,
        client: RetryingNotebookLMClient,
        title: Optional[str] = None,
    ) -> dict:
        if not self.matches(source):
            return {
                "source_id": None,
                "error": f"Source '{source}' did not match chain importer criteria.",
            }

        errors = []
        for idx, sub_importer in enumerate(self.importers):
            logger.debug(
                f"Chain importer executing sub-importer {idx} ({sub_importer.__class__.__name__}) on '{source}'"
            )
            res = await sub_importer.execute(
                notebook_id, source, client=client, title=title
            )
            if res.get("source_id"):
                return res
            errors.append(res.get("error", f"Sub-importer {idx} failed"))

        chained_errors = "; ".join(errors)
        return {
            "source_id": None,
            "error": f"All sub-importers in chain failed for '{source}': {chained_errors}",
        }


def build_importer(importer_cfg: ImporterConfig) -> Importer:
    """Constructs a concrete Importer (NativeImporter, ScraperImporter, or ChainImporter)
    from a resolved ImporterConfig.
    """
    match_rules = importer_cfg.match if importer_cfg.match else [".*"]

    if importer_cfg.native:
        return NativeImporter(
            config=importer_cfg.native,
            match_expressions=match_rules,
        )
    elif importer_cfg.scraper:
        return ScraperImporter(
            config=importer_cfg.scraper,
            match_expressions=match_rules,
        )
    elif importer_cfg.chain:
        sub_importers = [
            build_importer(sub)
            for sub in importer_cfg.chain.importers
            if isinstance(sub, ImporterConfig)
        ]
        return ChainImporter(
            importers=sub_importers,
            match_expressions=match_rules,
        )
    else:
        raise ValueError(
            f"Could not construct Importer from importer configuration: {importer_cfg}"
        )


async def execute_importer(
    importer: ImporterConfig,
    client: RetryingNotebookLMClient,
    notebook_id: str = "",
    source: str = "",
    title: Optional[str] = None,
) -> dict:
    """Executes an importer on a source string using the Importer interface."""
    imp_instance = build_importer(importer)
    return await imp_instance.execute(notebook_id, source, client=client, title=title)


async def scrape_source(
    url: str,
    tool: Optional[str] = None,
    command: Optional[str] = None,
    args: Optional[List[str]] = None,
    dry_run: bool = False,
    scraper_config: Optional[ScraperConfig] = None,
) -> Optional[dict]:
    """Scrapes a URL and returns the parsed agent metadata and content dictionary."""
    from jinja2 import Template

    if scraper_config:
        tool = tool or scraper_config.tool
        if scraper_config.agent:
            ag = scraper_config.agent
            command = command or ag.command
            args = args or ag.args or []

    if tool is None:
        tool = DEFAULT_SCRAPER_TOOL

    if command is None:
        raise ValueError(
            "No scraper agent command specified or configured in podcaster.yaml"
        )
    agent_command = command
    agent_args = args or []

    prompt = Template(SCRAPER_PROMPT_TEMPLATE).render(tool=tool, url=url)

    logger.info(f"Scraping URL via {agent_command}: {url}")

    context = {"prompt": prompt}
    cmd_args = [agent_command]
    for arg in agent_args:
        rendered = Template(arg).render(context)
        cmd_args.append(rendered)

    if dry_run:
        import shlex

        cmd_str = shlex.join(cmd_args)
        logger.info(f"Would execute: {cmd_str}")
        return None

    process = await asyncio.create_subprocess_exec(
        *cmd_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    stderr_lines: list[str] = []

    async def _read_stdout() -> bytes:
        if process.stdout:
            return await process.stdout.read()
        return b""

    async def _read_stderr() -> None:
        if process.stderr:
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                line_str = line.decode().rstrip()
                if line_str:
                    logger.info(f"[{os.path.basename(agent_command)}] {line_str}")
                    stderr_lines.append(line_str)

    stdout_bytes, _, _ = await asyncio.gather(
        _read_stdout(),
        _read_stderr(),
        process.wait(),
    )

    if process.returncode != 0:
        error_msg = "\n".join(stderr_lines).strip()
        raise RuntimeError(
            f"Scraping failed with exit code {process.returncode}: {error_msg}"
        )

    output = stdout_bytes.decode().strip()
    try:
        json_line = None
        for line in reversed(output.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                json_line = line
                break

        if not json_line:
            raise ValueError(
                f"Could not find valid JSON in {agent_command} output: {output}"
            )

        res = json.loads(json_line)
        if res.get("error"):
            raise RuntimeError(f"Scraper reported error: {res['error']}")

        return res
    except Exception as e:
        logger.error(f"Failed to parse scraper output: {output}")
        raise e


async def scrape(
    url: str,
    tool: Optional[str] = None,
    command: Optional[str] = None,
    args: Optional[List[str]] = None,
    dry_run: bool = False,
) -> Optional[dict]:
    """Scrapes a target URL and returns a dictionary containing the content and metadata.

    If dry_run is True, it logs the command that would be executed and returns None.
    """
    return await scrape_source(
        url, tool=tool, command=command, args=args, dry_run=dry_run
    )


def extract_drive_file_id(url: str) -> Optional[str]:
    """Extracts the Google Drive file ID from a URL."""
    match = re.search(r"/d/([^/]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"id=([^&]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"file/d/([^/]+)", url)
    if match:
        return match.group(1)
    return None


def evaluate_importer_match(match_expressions: List[str], source: str) -> bool:
    """Evaluates regex matcher expressions in order for a given source string.

    A leading '!' removes an existing match if the pattern matches.
    A positive expression sets match to True if the pattern matches.
    Initial state: matched = False.
    """
    matched = False
    for expr in match_expressions:
        if expr.startswith("!"):
            pattern = expr[1:]
            if re.search(pattern, source):
                matched = False
        else:
            pattern = expr
            if re.search(pattern, source):
                matched = True
    return matched


evaluate_handler_match = evaluate_importer_match


async def _import_native(
    notebook_id: str,
    source: str,
    client: RetryingNotebookLMClient,
    title: Optional[str] = None,
) -> str:
    if (
        "docs.google.com" in source
        or "drive.google.com" in source
        or source.startswith("gdrive:")
    ):
        if source.startswith("gdrive:"):
            file_id = source.split("gdrive:", 1)[1]
        else:
            file_id = extract_drive_file_id(source)

        if not file_id:
            raise ValueError(
                f"Could not extract Google Drive file ID from URL: {source}"
            )

        logger.debug(f"Native import: Google Drive file ID {file_id}")
        client_any: Any = client
        src_obj = await client_any.sources.add_drive(
            notebook_id,
            file_id,
            title=title or "Drive Source",
            wait=True,
            wait_timeout=600.0,
        )
        return src_obj.id

    clean_path = source
    if clean_path.startswith("file://"):
        clean_path = clean_path[7:]

    client_any: Any = client
    if clean_path and os.path.isfile(clean_path):
        logger.debug(f"Native import: Local file {clean_path}")
        src_obj = await client_any.sources.add_file(
            notebook_id, clean_path, wait=True, wait_timeout=600.0
        )
        return src_obj.id

    if source.startswith(("http://", "https://")):
        logger.debug(f"Native import: Web URL {source}")
        src_obj = await client_any.sources.add_url(notebook_id, source, wait=False)
        await client_any.sources.wait_until_ready(
            notebook_id, src_obj.id, timeout=600.0
        )
        return src_obj.id

    raise ValueError(f"Native importer cannot handle source format: {source}")


async def _import_scraper(
    notebook_id: str,
    source: str,
    client: RetryingNotebookLMClient,
    scraper_config: Optional[Any] = None,
    title: Optional[str] = None,
) -> str:
    if (
        not source.startswith(("http://", "https://"))
        or "docs.google.com" in source
        or "drive.google.com" in source
    ):
        raise ValueError(f"Scraper handler only supports web URLs, got: {source}")

    res = await scrape_source(source, scraper_config=scraper_config)
    if not res or not res.get("content"):
        error_msg = res.get("error") if res else "No response"
        raise RuntimeError(f"Scraping returned no content: {error_msg}")

    scraped_title = title or res.get("title") or "Untitled"
    scraped_author = res.get("author") or "Unknown"
    scraped_created_at = res.get("created_at") or "Unknown"
    scraped_url = res.get("url") or source

    file_content = (
        f"URL: {scraped_url}\n"
        f"Title: {scraped_title}\n"
        f"Author: {scraped_author}\n"
        f"Creation Date: {scraped_created_at}\n\n"
        f"{res.get('content', '')}"
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        safe_title = sanitize(scraped_title)
        temp_filepath = os.path.join(tmpdir, f"article_{safe_title}.txt")
        with open(temp_filepath, "w", encoding="utf-8") as f:
            f.write(file_content)

        client_any: Any = client
        src_obj = await client_any.sources.add_file(
            notebook_id, temp_filepath, wait=True, wait_timeout=600.0
        )
        return src_obj.id


async def import_source(
    notebook_id: str,
    source: str,
    importer: ImporterConfig,
    client: RetryingNotebookLMClient,
    title: Optional[str] = None,
) -> dict:
    """Import a source into NotebookLM using an already-open client."""
    source = normalize_source(source)
    return await execute_importer(
        importer, client=client, notebook_id=notebook_id, source=source, title=title
    )


async def import_web_source(
    notebook_id: str,
    url: str,
    importer: ImporterConfig,
    client: RetryingNotebookLMClient,
    title: Optional[str] = None,
) -> dict:
    """Delegates to import_source for generalized importing with fallback."""
    return await import_source(
        notebook_id, url, importer=importer, client=client, title=title
    )


async def create_research_job(
    notebook_id: str,
    source_id: str,
    client: RetryingNotebookLMClient,
    mode: str = "fast",
) -> ResearchTask:
    """Create a research job using an already-open NotebookLM client."""
    client_any: Any = client
    logger.debug(f"Fetching guide for source {source_id}...")
    guide = await client_any.sources.get_guide(notebook_id, source_id)
    keywords = guide.get("keywords", []) if isinstance(guide, dict) else []
    if not keywords:
        logger.debug(f"No keywords found in guide for source {source_id}.")
        topic = DEFAULT_RESEARCH_TOPIC
    else:
        topic = ", ".join(keywords)

    logger.debug(
        f"Generating summary and duration suggestion for source {source_id}..."
    )
    summary_res: Any = await client_any.chat.ask(
        notebook_id, RESEARCH_SUMMARY_PROMPT, source_ids=[source_id]
    )

    answer_text = str(getattr(summary_res, "answer", summary_res))
    summary, suggested_duration = parse_summary_response(answer_text)

    if not suggested_duration or parse_duration_minutes(suggested_duration) is None:
        logger.warning(
            f"Could not parse suggested duration {suggested_duration!r}, defaulting to {DEFAULT_RESEARCH_DURATION}."
        )
        suggested_duration = DEFAULT_RESEARCH_DURATION

    prompt = f"Topic: {topic}. Context: {summary}"
    logger.debug(f"Starting research with prompt: {prompt} (mode: {mode})")
    job: Any = await client_any.research.start(notebook_id, prompt, mode=mode)
    if not job:
        raise RuntimeError("Failed to start research job.")

    task_id = (
        job.get("task_id") if isinstance(job, dict) else getattr(job, "task_id", "")
    ) or ""
    return ResearchTask(
        notebook_id=notebook_id,
        source_id=source_id,
        task_id=task_id,
        topic=topic,
        summary=summary,
        suggested_duration=suggested_duration,
    )


async def poll_research_jobs(
    tasks: AsyncIterable[ResearchTask],
    notebooklm_config: NotebookLMConfig,
    fallback_importer: Optional[ImporterConfig] = None,
    max_import_failures: Optional[int] = None,
) -> AsyncGenerator[ResearchResult, None]:
    async with get_notebooklm_client(notebooklm_config) as raw_client:
        client: Any = raw_client
        async for t in tasks:
            notebook_id = t.notebook_id
            task_id = t.task_id

            # 5. Poll for completion
            started_at = time.time()
            while True:
                try:
                    res: Any = await client.research.poll(notebook_id)
                    status = (
                        res.get("status")
                        if isinstance(res, dict)
                        else getattr(res, "status", None)
                    )
                    logger.debug(f"Research status: {status}")

                    sources_list_raw = (
                        res.get("sources", [])
                        if isinstance(res, dict)
                        else getattr(res, "sources", [])
                    )
                    if status == TaskStatus.COMPLETED or (
                        status == TaskStatus.IN_PROGRESS and len(sources_list_raw) > 0
                    ):
                        found_sources = sources_list_raw
                        break
                    elif status == TaskStatus.FAILED:
                        raise RuntimeError(f"Research job failed: {res}")
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
                        raise e
                    logger.warning(
                        f"Transient network error polling research for notebook {notebook_id}: {e}"
                    )

                if time.time() - started_at > 600.0:
                    raise TimeoutError(
                        f"Research polling timed out after 600s for notebook {notebook_id}"
                    )

                await asyncio.sleep(5)

            logger.debug(f"Found {len(found_sources)} sources.")

            # 6. Select sources to import
            sources_to_import: list[ResearchSource] = found_sources

            # 7. Import sources
            imported = []
            failed_imports_count = 0
            if sources_to_import:
                logger.debug(f"Importing {len(sources_to_import)} sources...")
                for src in sources_to_import:
                    src_url = src.url
                    src_title = src.title
                    import_success = False
                    try:
                        single_imported: Any = await client.research.import_sources(
                            notebook_id, task_id, [src]
                        )
                        if isinstance(single_imported, list) and single_imported:
                            imported.extend(single_imported)
                            import_success = True
                        elif single_imported:
                            imported.append(single_imported)
                            import_success = True
                    except Exception as e:
                        logger.warning(
                            f"NotebookLM import failed for research source '{src_url}': {e}"
                        )

                    if not import_success and fallback_importer is not None:
                        try:
                            imp_res = await import_source(
                                notebook_id,
                                src_url,
                                importer=fallback_importer,
                                client=client,
                                title=src_title,
                            )
                            if imp_res.get("source_id"):
                                imported.append(
                                    {"id": imp_res["source_id"], "url": src_url}
                                )
                                import_success = True
                            else:
                                logger.warning(
                                    f"Fallback import failed for '{src_url}': {imp_res.get('error')}"
                                )
                        except Exception as fe:
                            logger.warning(
                                f"Fallback import exception for '{src_url}': {fe}"
                            )

                    if not import_success:
                        failed_imports_count += 1
                        logger.info(
                            f"Ignoring/cleaning up failed research source: {src_url}"
                        )
                        try:
                            sources_list: Any = await client.sources.list(notebook_id)
                            for existing_src in sources_list or []:
                                src_u = (
                                    existing_src.url
                                    if hasattr(existing_src, "url") and existing_src.url
                                    else getattr(existing_src, "title", "")
                                )
                                src_status = (
                                    existing_src.status
                                    if hasattr(existing_src, "status")
                                    else None
                                )
                                if src_u == src_url or src_status == "error":
                                    logger.info(
                                        f"Removing failed/errored source from NotebookLM: {existing_src.id}"
                                    )
                                    await client.sources.delete(
                                        notebook_id, existing_src.id
                                    )
                        except Exception as delete_err:
                            logger.debug(
                                f"Error checking/deleting failed source: {delete_err}"
                            )

                        if (
                            max_import_failures is not None
                            and failed_imports_count > max_import_failures
                        ):
                            raise RuntimeError(
                                f"Research import failed: {failed_imports_count} failed import(s) exceeded max_import_failures limit ({max_import_failures})."
                            )

            yield ResearchResult(
                notebook_id=t.notebook_id,
                source_id=t.source_id,
                task_id=t.task_id,
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
    mode: str = "fast",
    task_id: Optional[str] = None,
    topic: Optional[str] = None,
    summary: Optional[str] = None,
    suggested_duration: Optional[str] = None,
    on_start_callback: Optional[Callable[[str, str, str, str], Any]] = None,
    fallback_importer: Optional[ImporterConfig] = None,
    max_import_failures: Optional[int] = None,
) -> ResearchResult:

    if not task_id:
        task = await create_research_job(
            notebook_id, source_id, client=client, mode=mode
        )
        task_id = task.task_id
        topic = task.topic
        summary = task.summary
        suggested_duration = task.suggested_duration
        if on_start_callback:
            cb_res = on_start_callback(task_id, topic, summary, suggested_duration)
            if asyncio.iscoroutine(cb_res) or hasattr(cb_res, "__await__"):
                await cb_res

    assert task_id is not None
    assert topic is not None
    assert summary is not None
    assert suggested_duration is not None

    task_obj = ResearchTask(
        notebook_id=notebook_id,
        source_id=source_id,
        task_id=task_id,
        topic=topic,
        summary=summary,
        suggested_duration=suggested_duration,
    )

    async def task_gen():
        yield task_obj

    res = None
    async for r in poll_research_jobs(
        task_gen(),
        notebooklm_config,
        fallback_importer=fallback_importer,
        max_import_failures=max_import_failures,
    ):
        res = r
    if res is None:
        raise RuntimeError("Research polling failed to return a result.")
    return res
