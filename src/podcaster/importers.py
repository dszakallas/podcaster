"""Source importing interfaces and implementations for NotebookLM."""

import logging
import os
import re
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .config import ImporterConfig
from .scrapers import DEFAULT_SCRAPER_SHUTDOWN_GRACE_PERIOD, scrape_source
from .utils.files import sanitize
from .utils.notebooklm import RetryingNotebookLMClient

logger = logging.getLogger(__name__)

DEFAULT_IMPORTER_KEY = "default"


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


def extract_drive_file_id(url: str) -> str | None:
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


def evaluate_importer_match(match_expressions: list[str], source: str) -> bool:
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


async def _import_native(
    notebook_id: str,
    source: str,
    client: RetryingNotebookLMClient,
    title: str | None = None,
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
        src_obj = await client.sources.add_drive(
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

    if clean_path and os.path.isfile(clean_path):
        logger.debug(f"Native import: Local file {clean_path}")
        src_obj = await client.sources.add_file(
            notebook_id, clean_path, wait=True, wait_timeout=600.0
        )
        return src_obj.id

    if source.startswith(("http://", "https://")):
        logger.debug(f"Native import: Web URL {source}")
        src_obj = await client.sources.add_url(notebook_id, source, wait=False)
        await client.sources.wait_until_ready(notebook_id, src_obj.id, timeout=600.0)
        return src_obj.id

    raise ValueError(f"Native importer cannot handle source format: {source}")


async def _import_scraper(
    notebook_id: str,
    source: str,
    client: RetryingNotebookLMClient,
    scraper_config: Any | None = None,
    title: str | None = None,
    timeout: float | None = None,
    grace_period: float = DEFAULT_SCRAPER_SHUTDOWN_GRACE_PERIOD,
) -> str:
    if (
        not source.startswith(("http://", "https://"))
        or "docs.google.com" in source
        or "drive.google.com" in source
    ):
        raise ValueError(f"Scraper handler only supports web URLs, got: {source}")

    res = await scrape_source(
        source,
        scraper_config=scraper_config,
        timeout=timeout,
        grace_period=grace_period,
    )
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

        src_obj = await client.sources.add_file(
            notebook_id, temp_filepath, wait=True, wait_timeout=600.0
        )
        return src_obj.id


class Importer(ABC):
    """Abstract interface for all importers."""

    def __init__(self, match_expressions: list[str] | None = None):
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
        title: str | None = None,
    ) -> dict:
        """Executes the import operation on a given source string."""
        ...


class NativeImporter(Importer):
    """Native importer utilizing NotebookLM's built-in file/URL/Drive importer."""

    def __init__(
        self,
        config: Any | None = None,
        match_expressions: list[str] | None = None,
    ):
        super().__init__(match_expressions=match_expressions)
        self.config = config

    async def execute(
        self,
        notebook_id: str,
        source: str,
        client: RetryingNotebookLMClient,
        title: str | None = None,
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
        config: Any | None = None,
        match_expressions: list[str] | None = None,
        timeout: float | None = None,
        grace_period: float = DEFAULT_SCRAPER_SHUTDOWN_GRACE_PERIOD,
    ):
        super().__init__(match_expressions=match_expressions)
        self.config = config
        self.timeout = timeout
        self.grace_period = grace_period

    async def execute(
        self,
        notebook_id: str,
        source: str,
        client: RetryingNotebookLMClient,
        title: str | None = None,
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
                timeout=self.timeout,
                grace_period=self.grace_period,
            )
            return {"source_id": src_id, "importer": "scraper"}
        except Exception as e:
            return {"source_id": None, "error": f"[scraper]: {e}"}


class ChainImporter(Importer):
    """Composite importer executing a chain of sub-importers in priority order."""

    def __init__(
        self,
        importers: list[Importer] | None = None,
        match_expressions: list[str] | None = None,
    ):
        super().__init__(match_expressions=match_expressions)
        self.importers = importers or []

    async def execute(
        self,
        notebook_id: str,
        source: str,
        client: RetryingNotebookLMClient,
        title: str | None = None,
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
    """Constructs a concrete Importer from a resolved ImporterConfig."""
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
    title: str | None = None,
) -> dict:
    """Executes an importer on a source string using the Importer interface."""
    imp_instance = build_importer(importer)
    return await imp_instance.execute(notebook_id, source, client=client, title=title)


async def import_source(
    notebook_id: str,
    source: str,
    importer: ImporterConfig,
    client: RetryingNotebookLMClient,
    title: str | None = None,
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
    title: str | None = None,
) -> dict:
    """Delegates to import_source for generalized importing with fallback."""
    return await import_source(
        notebook_id, url, importer=importer, client=client, title=title
    )
