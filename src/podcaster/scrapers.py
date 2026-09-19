"""Agent-driven web scraping functionality."""

import json
import logging
import os
import shlex

from jinja2 import Template

from .config import ScraperConfig
from .utils.process import (
    DEFAULT_SHUTDOWN_GRACE_PERIOD,
    run_process,
)

logger = logging.getLogger(__name__)

DEFAULT_SCRAPER_TOOL = "playwright"
DEFAULT_SCRAPER_SHUTDOWN_GRACE_PERIOD = DEFAULT_SHUTDOWN_GRACE_PERIOD

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


def _parse_scraper_output(output: str, agent_command: str) -> dict:
    """Parse and validate JSON payload from scraper process output."""
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


async def scrape_source(
    url: str,
    tool: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    dry_run: bool = False,
    scraper_config: ScraperConfig | None = None,
    timeout: float | None = None,
    grace_period: float = DEFAULT_SCRAPER_SHUTDOWN_GRACE_PERIOD,
) -> dict | None:
    """Scrapes a URL and returns the parsed agent metadata and content dictionary."""
    if scraper_config:
        tool = tool or scraper_config.tool
        if timeout is None:
            timeout = scraper_config.timeout
        if scraper_config.agent:
            ag = scraper_config.agent
            command = command or getattr(ag, "command", None)
            args = args or getattr(ag, "args", None) or []
            if timeout is None:
                timeout = getattr(ag, "timeout", None)

    if timeout is not None and timeout <= 0:
        raise ValueError(f"Timeout must be positive, got {timeout}")

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
        cmd_str = shlex.join(cmd_args)
        logger.info(f"Would execute: {cmd_str}")
        return None

    def _log_stderr_line(line_str: str) -> None:
        logger.info(f"[{os.path.basename(agent_command)}] {line_str}")

    result = await run_process(
        cmd_args,
        timeout=timeout,
        grace_period=grace_period,
        on_stderr_line=_log_stderr_line,
        process_name="Scraper process",
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Scraping failed with exit code {result.returncode}: {result.stderr}"
        )

    output = result.stdout_text().strip()
    return _parse_scraper_output(output, agent_command)


async def scrape(
    url: str,
    tool: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    dry_run: bool = False,
    timeout: float | None = None,
    grace_period: float = DEFAULT_SCRAPER_SHUTDOWN_GRACE_PERIOD,
) -> dict | None:
    """Scrapes a target URL and returns a dictionary containing the content and metadata.

    If dry_run is True, it logs the command that would be executed and returns None.
    """
    return await scrape_source(
        url,
        tool=tool,
        command=command,
        args=args,
        dry_run=dry_run,
        timeout=timeout,
        grace_period=grace_period,
    )
