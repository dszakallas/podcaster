import asyncio
import json
import logging
import os
import random
import re
from typing import Any, AsyncGenerator, Callable, List, Optional

from notebooklm import NotebookLMClient

from .utils import get_storage_path, load_config, setup_logging

logger = logging.getLogger(__name__)


async def scrape_source(
    url: str,
    tool: Optional[str] = None,
    command: Optional[str] = None,
    args: Optional[List[str]] = None,
) -> str:
    """Scrapes a URL and returns the path to the saved text file using the configured agent tool."""
    from jinja2 import Template

    from .utils import sanitize

    config = load_config()
    if tool is None:
        tool = getattr(config.scraper, "tool", "playwright")

    if command is None:
        if config.scraper.ref:
            agent_def = config.agents.get(config.scraper.ref)
            if not agent_def:
                raise ValueError(
                    f"Agent reference '{config.scraper.ref}' not found in top-level agents configuration."
                )
            agent_command = agent_def.command
            agent_args = agent_def.args
        elif config.scraper.command:
            agent_command = config.scraper.command
            agent_args = config.scraper.args or []
        else:
            agent_command = "gemini"
            agent_args = ["-p", "{{ prompt }}"]
    else:
        agent_command = command
        agent_args = args or []

    # Ensure .tmp directory exists
    tmp_dir = ".tmp"
    os.makedirs(tmp_dir, exist_ok=True)

    # Generate a unique filename based on the URL
    base_name = url.split("/")[-1].split("?")[0] or "web_article"
    safe_name = sanitize(base_name)
    filename = os.path.join(tmp_dir, f"article_{safe_name}.txt")

    prompt = (
        f'Use the {tool} MCP tool to navigate to the URL "{url}". '
        "If you encounter bot detection, cookie consent banners, paywalls, or blank pages, attempt to bypass them. "
        "Useful techniques include: waiting for elements, dismissing cookie/consent popups, scrolling the page "
        "naturally, and emulating a common device viewport or user agent if supported by the tool. "
        f'Extract the article content and save it to the file "{filename}" '
        "in the current workspace. At the very top of the file, before the article text, include the following metadata: "
        "URL, Title, Author, and Creation Date, each on its own line. "
        "In addition, find all hyperlinks within the article body that are "
        "useful for content enrichment. Extract 3-5 keywords summarizing the article's topics, and find "
        "the publication date. Close the browser tab when finished. "
        "Finally, respond with a single, valid NDJSON object on a single line without whitespace in this exact "
        'format: {"filename":"'
        + filename.replace("\\", "\\\\")
        + '","links":[...],"keywords":[...],"created_at":"<date>","url":"..."}. '
        "If any failure occurs, return instead: "
        '{"filename":null,"error":"<error description>"}. Make sure that the browser is closed in the end. '
        "Do not output any markdown code blocks or additional text."
    )

    logger.info(f"Scraping URL via {agent_command}: {url} -> {filename}")

    context = {"prompt": prompt}
    cmd_args = [agent_command]
    for arg in agent_args:
        rendered = Template(arg).render(context)
        cmd_args.append(rendered)

    process = await asyncio.create_subprocess_exec(
        *cmd_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode().strip()
        raise RuntimeError(
            f"Scraping failed with exit code {process.returncode}: {error_msg}"
        )

    output = stdout.decode().strip()
    try:
        # agy might output some log lines before the final NDJSON, let's look for the JSON line
        # but the prompt specifically asks for a single line of NDJSON without whitespace.
        # However, it's safer to find the last line that looks like JSON.
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

        final_filename = res.get("filename")
        if not final_filename or not os.path.exists(final_filename):
            raise FileNotFoundError(
                f"Scraper claimed success but file {final_filename} was not found."
            )

        return final_filename
    except Exception as e:
        logger.error(f"Failed to parse scraper output: {output}")
        raise e


def extract_drive_file_id(url: str) -> Optional[str]:
    """Extracts the Google Drive file ID from a URL."""
    # Docs/Slides/Sheets pattern: /d/<ID>/
    match = re.search(r"/d/([^/]+)", url)
    if match:
        return match.group(1)
    # Generic drive/file/d/<ID> pattern
    match = re.search(r"id=([^&]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"file/d/([^/]+)", url)
    if match:
        return match.group(1)
    return None


async def import_web_source(
    notebook_id: str,
    url: str,
    unimportables: Optional[List[re.Pattern]] = None,
    fallback_mode: str = "scrape",
    title: Optional[str] = None,
    tool: Optional[str] = None,
    command: Optional[str] = None,
    args: Optional[List[str]] = None,
) -> dict:
    """
    Imports a web URL or Google Drive link as a source to a notebook with fallback handling.
    Returns a dict with source_id and potentially an error or filename.
    """
    config = load_config()

    # Check if it's a Google Drive link
    if "docs.google.com" in url or "drive.google.com" in url:
        file_id = extract_drive_file_id(url)
        if file_id:
            logger.debug(f"Detected Google Drive URL, extracting file ID: {file_id}")
            storage_path = get_storage_path()
            async with await NotebookLMClient.from_storage(
                storage_path, timeout=120.0
            ) as client:
                try:
                    # We use a placeholder title if none provided, NotebookLM will likely rename it
                    source = await client.sources.add_drive(
                        notebook_id,
                        file_id,
                        title=title or "Drive Source",
                        wait=True,
                        wait_timeout=600.0,
                    )
                    return {"source_id": source.id, "drive": True}
                except Exception as e:
                    return {"source_id": None, "error": str(e)}

    if unimportables is None:
        patterns = config.research.unimportables
        unimportables = [re.compile(p, re.IGNORECASE) for p in patterns]

    if fallback_mode is None:
        fallback_mode = config.research.import_fallback

    # Check if URL matches any unimportable pattern
    is_unimportable = any(p.search(url) for p in unimportables)

    if is_unimportable:
        logger.debug(f"URL matches unimportable pattern: {url}")
        if fallback_mode == "ignore":
            logger.info(f"Ignoring unimportable URL: {url}")
            return {"source_id": None, "ignored": True}
        elif fallback_mode == "scrape":
            filename = await scrape_source(url, tool=tool, command=command, args=args)
            # Upload the scraped file as a text source
            storage_path = get_storage_path()
            async with await NotebookLMClient.from_storage(
                storage_path, timeout=120.0
            ) as client:
                source = await client.sources.add_file(
                    notebook_id, filename, wait=True, wait_timeout=600.0
                )
                return {"source_id": source.id, "scraped": True, "filename": filename}
        # "force" continues below

    storage_path = get_storage_path()
    async with await NotebookLMClient.from_storage(
        storage_path, timeout=120.0
    ) as client:
        try:
            source = await client.sources.add_url(notebook_id, url, wait=False)
            source_id = source.id
            await client.sources.wait_until_ready(notebook_id, source_id, timeout=600.0)
            return {"source_id": source_id}
        except Exception as e:
            return {"source_id": None, "error": str(e)}


async def create_research_job(
    notebook_id: str,
    source_id: str,
    mode: str = "fast",
) -> dict:
    storage_path = get_storage_path()
    async with await NotebookLMClient.from_storage(
        storage_path, timeout=120.0
    ) as client:
        # 1. Fetch source guide for keywords
        logger.debug(f"Fetching guide for source {source_id}...")
        guide = await client.sources.get_guide(notebook_id, source_id)
        keywords = guide.get("keywords", [])
        if not keywords:
            logger.debug(f"No keywords found in guide for source {source_id}.")
            topic = "general"  # Fallback topic
        else:
            topic = ", ".join(keywords)

        # 2. Get one-sentence summary and suggested length
        logger.debug(
            f"Generating summary and length suggestion for source {source_id}..."
        )
        summary_q = (
            "1. Summarize this source in exactly one sentence with dates for important events.\n"
            "2. Suggest a podcast length for a deep dive into this article. Choose ONLY one from: 'short', 'default', 'long'.\n"
            'Respond in NDJSON format: {"summary": "...", "suggested_length": "..."}'
        )
        summary_res = await client.chat.ask(
            notebook_id, summary_q, source_ids=[source_id]
        )

        try:
            # Find the JSON block in the answer
            answer_text = summary_res.answer
            match = re.search(r"\{.*\}", answer_text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                summary = data.get("summary", answer_text)
                suggested_length = data.get("suggested_length", "long")
            else:
                summary = answer_text
                suggested_length = "long"
        except Exception:
            logger.warning(
                "Failed to parse summary and length from response, using defaults."
            )
            summary = summary_res.answer
            suggested_length = "long"

        if suggested_length not in ["short", "default", "long"]:
            suggested_length = "long"

        # 3. Assemble research prompt
        prompt = f"Topic: {topic}. Context: {summary}"
        logger.debug(f"Starting research with prompt: {prompt} (mode: {mode})")

        # 4. Start research
        job = await client.research.start(notebook_id, prompt, mode=mode)
        if not job:
            raise RuntimeError("Failed to start research job.")

        task_id = job.get("task_id")

        return {
            "notebook_id": notebook_id,
            "source_id": source_id,
            "task_id": task_id,
            "topic": topic,
            "summary": summary,
            "suggested_length": suggested_length,
            "status": "pending",
            "type": "research",
        }


async def poll_research_jobs(
    tasks: AsyncGenerator[dict, None],
    max_imports: Optional[int] = None,
) -> AsyncGenerator[dict, None]:
    storage_path = get_storage_path()
    async with await NotebookLMClient.from_storage(
        storage_path, timeout=120.0
    ) as client:
        async for task in tasks:
            notebook_id = task["notebook_id"]
            task_id = task["task_id"]

            # 5. Poll for completion
            while True:
                res = await client.research.poll(notebook_id)
                status = res.get("status")
                logger.debug(f"Research status: {status}")

                if status == "completed" or (
                    status == "in_progress" and len(res.get("sources", [])) > 0
                ):
                    found_sources = res.get("sources", [])
                    break
                elif status == "failed":
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
                imported = await client.research.import_sources(
                    notebook_id, task_id, sources_to_import
                )

            yield {
                **task,
                "status": "completed",
                "found_count": len(found_sources),
                "imported_count": len(imported),
                "imported": imported,
            }


async def research_from_source(
    notebook_id: str,
    source_id: str,
    mode: str = "fast",
    max_imports: Optional[int] = None,
    verbose: bool = False,
    task_id: Optional[str] = None,
    topic: Optional[str] = None,
    summary: Optional[str] = None,
    suggested_length: Optional[str] = None,
    on_start_callback: Optional[Callable[[str, str, str, str], Any]] = None,
) -> dict:
    if verbose:
        setup_logging(verbose)

    if not task_id:
        task = await create_research_job(notebook_id, source_id, mode)
        task_id = task["task_id"]
        topic = task["topic"]
        summary = task["summary"]
        suggested_length = task["suggested_length"]
        if on_start_callback:
            await on_start_callback(task_id, topic, summary, suggested_length)

    async def task_gen():
        yield {
            "notebook_id": notebook_id,
            "source_id": source_id,
            "task_id": task_id,
            "topic": topic,
            "summary": summary,
            "suggested_length": suggested_length,
        }

    res = None
    async for r in poll_research_jobs(task_gen(), max_imports):
        res = r
    if res is None:
        raise RuntimeError("Research polling failed to return a result.")
    return res
