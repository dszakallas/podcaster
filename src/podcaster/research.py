import asyncio
import json
import logging
import os
import random
import re
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, AsyncGenerator, AsyncIterable, Callable, List, Optional, Union

from .config import ImporterConfig
from .models import ResearchResult, ResearchTask, TaskStatus
from .utils import (
    RetryingNotebookLMClient,
    get_notebooklm_client,
    load_config,
    parse_duration_minutes,
    sanitize,
)

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
    'Respond in NDJSON format: {"summary": "...", "suggested_duration": "..."}'
)


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
        title: Optional[str] = None,
        client: Optional[RetryingNotebookLMClient] = None,
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
        title: Optional[str] = None,
        client: Optional[RetryingNotebookLMClient] = None,
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
        title: Optional[str] = None,
        client: Optional[RetryingNotebookLMClient] = None,
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
        title: Optional[str] = None,
        client: Optional[RetryingNotebookLMClient] = None,
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
                notebook_id, source, title=title, client=client
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
    importer_input: Any = None,
    notebook_id: str = "",
    source: str = "",
    title: Optional[str] = None,
    client: Optional[RetryingNotebookLMClient] = None,
) -> dict:
    """Executes an importer on a source string using the Importer interface."""
    config = load_config()
    if isinstance(importer_input, ImporterConfig):
        importer_cfg = importer_input
    else:
        name = str(importer_input or "default")
        if name not in config.importers:
            raise ValueError(f"Importer '{name}' not found in configuration.")
        importer_cfg = config.importers[name]
    importer_obj = build_importer(importer_cfg)
    return await importer_obj.execute(notebook_id, source, title=title, client=client)


async def scrape_source(
    url: str,
    tool: Optional[str] = None,
    command: Optional[str] = None,
    args: Optional[List[str]] = None,
    dry_run: bool = False,
) -> Optional[dict]:
    """Scrapes a URL and returns the parsed agent metadata and content dictionary."""
    from jinja2 import Template

    config = load_config()

    if tool is None or command is None:
        scraper_def = None
        if "default" in config.scrapers:
            scraper_def = config.scrapers["default"]
        elif config.scrapers:
            scraper_def = next(iter(config.scrapers.values()))

        if tool is None and scraper_def:
            tool = scraper_def.tool

        if command is None and scraper_def and scraper_def.agent:
            ag = scraper_def.agent
            if ag.ref and ag.ref in config.agents:
                agent_def = config.agents[ag.ref]
                command = agent_def.command
                args = agent_def.args
            elif ag.command:
                command = ag.command
                args = ag.args or []

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
    title: Optional[str] = None,
    client: Optional[RetryingNotebookLMClient] = None,
) -> str:
    if client is None:
        raise ValueError("Client is required for _import_native")

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
    scraper_config: Optional[Any] = None,
    title: Optional[str] = None,
    client: Optional[RetryingNotebookLMClient] = None,
) -> str:
    if client is None:
        raise ValueError("Client is required for _import_scraper")

    if (
        not source.startswith(("http://", "https://"))
        or "docs.google.com" in source
        or "drive.google.com" in source
    ):
        raise ValueError(f"Scraper handler only supports web URLs, got: {source}")

    tool = None
    command = None
    args = None
    config = load_config()

    if scraper_config:
        scraper_def = None
        # scraper_config is a resolved ScraperConfig
        scraper_def = scraper_config

        if scraper_def:
            tool = scraper_def.tool
            if scraper_def.agent:
                ag = scraper_def.agent
                if ag.ref and ag.ref in config.agents:
                    agent_def = config.agents[ag.ref]
                    command = agent_def.command
                    args = agent_def.args
                elif ag.command:
                    command = ag.command
                    args = ag.args or []

        if scraper_config.tool:
            tool = scraper_config.tool

        ag_inline = scraper_config.agent
        if ag_inline:
            if ag_inline.ref and ag_inline.ref in config.agents:
                agent_def = config.agents[ag_inline.ref]
                command = agent_def.command
                args = agent_def.args
            elif ag_inline.command:
                command = ag_inline.command
                args = ag_inline.args or []

    res = await scrape_source(source, tool=tool, command=command, args=args)
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
    importer: Optional[Union[str, Any]] = "default",
    title: Optional[str] = None,
    client: Optional[RetryingNotebookLMClient] = None,
) -> dict:
    """Imports a source into NotebookLM using configured importer (native, scraper, or chain)."""
    source = normalize_source(source)

    if client is not None:
        return await execute_importer(
            importer, notebook_id, source, title=title, client=client
        )

    async with get_notebooklm_client() as c:
        return await execute_importer(
            importer, notebook_id, source, title=title, client=c
        )


async def import_web_source(
    notebook_id: str,
    url: str,
    title: Optional[str] = None,
    importer: str = "default",
) -> dict:
    """Delegates to import_source for generalized importing with fallback."""
    return await import_source(notebook_id, url, importer=importer, title=title)


async def create_research_job(
    notebook_id: str,
    source_id: str,
    mode: str = "fast",
) -> ResearchTask:
    async with get_notebooklm_client() as raw_client:
        client: Any = raw_client
        # 1. Fetch source guide for keywords
        logger.debug(f"Fetching guide for source {source_id}...")
        guide = await client.sources.get_guide(notebook_id, source_id)
        keywords = guide.get("keywords", []) if isinstance(guide, dict) else []
        if not keywords:
            logger.debug(f"No keywords found in guide for source {source_id}.")
            topic = DEFAULT_RESEARCH_TOPIC  # Fallback topic
        else:
            topic = ", ".join(keywords)

        # 2. Get one-sentence summary and suggested duration
        logger.debug(
            f"Generating summary and duration suggestion for source {source_id}..."
        )
        summary_q = RESEARCH_SUMMARY_PROMPT
        summary_res: Any = await client.chat.ask(
            notebook_id, summary_q, source_ids=[source_id]
        )

        try:
            # Find the JSON block in the answer
            answer_text = str(getattr(summary_res, "answer", summary_res))
            match = re.search(r"\{.*\}", answer_text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                summary = data.get("summary", answer_text)
                suggested_duration = data.get("suggested_duration", "")
            else:
                summary = answer_text
                suggested_duration = ""
        except Exception:
            logger.warning(
                "Failed to parse summary and duration from response, using defaults."
            )
            summary = str(getattr(summary_res, "answer", summary_res))
            suggested_duration = ""

        if not suggested_duration or parse_duration_minutes(suggested_duration) is None:
            logger.warning(
                f"Could not parse suggested duration {suggested_duration!r}, defaulting to {DEFAULT_RESEARCH_DURATION}."
            )
            suggested_duration = DEFAULT_RESEARCH_DURATION

        # 3. Assemble research prompt
        prompt = f"Topic: {topic}. Context: {summary}"
        logger.debug(f"Starting research with prompt: {prompt} (mode: {mode})")

        # 4. Start research
        job: Any = await client.research.start(notebook_id, prompt, mode=mode)
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
    max_imports: Optional[int] = None,
    fallback_mechanism: str = "ignore",
) -> AsyncGenerator[ResearchResult, None]:
    async with get_notebooklm_client() as raw_client:
        client: Any = raw_client
        async for t in tasks:
            notebook_id = t.notebook_id
            task_id = t.task_id

            # 5. Poll for completion
            while True:
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

                await asyncio.sleep(5)

            logger.debug(f"Found {len(found_sources)} sources.")

            # 6. Select sources to import
            sources_to_import = found_sources
            if max_imports is not None and len(found_sources) > max_imports:
                sources_to_import = random.sample(found_sources, max_imports)
                logger.debug(
                    f"Randomly selected {len(sources_to_import)} sources to import."
                )

            # 7. Import sources
            imported = []
            if sources_to_import:
                logger.debug(f"Importing {len(sources_to_import)} sources...")
                for src in sources_to_import:
                    src_url = str(
                        src.get("url") if isinstance(src, dict) else src or ""
                    )
                    try:
                        single_imported: Any = await client.research.import_sources(
                            notebook_id, task_id, [src]
                        )
                        if isinstance(single_imported, list):
                            imported.extend(single_imported)
                        elif single_imported:
                            imported.append(single_imported)
                    except Exception as e:
                        logger.warning(
                            f"NotebookLM import failed for research source '{src_url}': {e}"
                        )
                        if fallback_mechanism != "ignore":
                            try:
                                imp_res = await import_source(
                                    notebook_id,
                                    src_url,
                                    importer=fallback_mechanism,
                                    client=client,
                                )
                                if imp_res.get("source_id"):
                                    imported.append(
                                        {"id": imp_res["source_id"], "url": src_url}
                                    )
                                else:
                                    logger.warning(
                                        f"Fallback import failed for '{src_url}': {imp_res.get('error')}"
                                    )
                            except Exception as fe:
                                logger.warning(
                                    f"Fallback import exception for '{src_url}': {fe}"
                                )
                        else:
                            logger.info(f"Ignoring failed research source: {src_url}")
                            try:
                                sources_list: Any = await client.sources.list(
                                    notebook_id
                                )
                                for existing_src in sources_list or []:
                                    src_u = (
                                        existing_src.url
                                        if hasattr(existing_src, "url")
                                        and existing_src.url
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
    mode: str = "fast",
    max_imports: Optional[int] = None,
    task_id: Optional[str] = None,
    topic: Optional[str] = None,
    summary: Optional[str] = None,
    suggested_duration: Optional[str] = None,
    on_start_callback: Optional[Callable[[str, str, str, str], Any]] = None,
    fallback_mechanism: str = "ignore",
) -> ResearchResult:

    if not task_id:
        task = await create_research_job(notebook_id, source_id, mode)
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
        max_imports,
        fallback_mechanism=fallback_mechanism,
    ):
        res = r
    if res is None:
        raise RuntimeError("Research polling failed to return a result.")
    return res
