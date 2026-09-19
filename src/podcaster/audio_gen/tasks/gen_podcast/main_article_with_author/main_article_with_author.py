import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from notebooklm import NotebookLMClient

from podcaster.audio_gen.prompting import (
    query_llm_json,
    render_prompt_template,
    resolve_two_roles,
)
from podcaster.utils.notebooklm import RetryingNotebookLMClient

from ....params import StandardPodcastTaskInputs

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ....params import AudioGenParams


class Inputs(StandardPodcastTaskInputs):
    pass


async def resolve_best_matching_source_id(
    client: RetryingNotebookLMClient | NotebookLMClient,
    notebook_id: str,
    target_topic: str,
) -> str:
    """Resolve the source ID in the notebook whose summary matches the article topic best."""
    sources = await client.sources.list(notebook_id)
    if not sources:
        raise ValueError(f"No sources found in notebook {notebook_id}")
    if len(sources) == 1:
        return sources[0].id

    async def _fetch_guide(s):
        try:
            guide = await client.sources.get_guide(notebook_id, s.id)
            summary = guide.summary or ""
        except Exception as e:
            logger.debug(f"Failed to fetch guide for source {s.id}: {e}")
            summary = ""
        return s, summary

    source_infos = await asyncio.gather(*[_fetch_guide(s) for s in sources])

    candidate_lines = [
        f"Source ID: {s.id}\nTitle: {s.title}\nSummary: {summary[:350]}"
        for s, summary in source_infos
    ]
    prompt = (
        f"Based on the following sources in this notebook, identify the single source that best matches the article topic: '{target_topic}'.\n\n"
        + "\n\n".join(candidate_lines)
        + '\n\nRespond strictly in JSON format with the source_id of the best match: {"source_id": "<source_id>"}'
    )

    valid_ids = {s.id for s, _ in source_infos}
    try:
        data = await query_llm_json(client, notebook_id, prompt)
        suggested_id = data.get("source_id")
        if suggested_id is not None and suggested_id in valid_ids:
            logger.info(
                f"Resolved source {suggested_id} via NotebookLM chat for topic '{target_topic}'"
            )
            return str(suggested_id)
    except Exception as e:
        logger.debug(f"NotebookLM chat source resolution failed: {e}")

    # Fallback to word-overlap scoring against title and summary
    def _score(s, summary: str) -> float:
        target_tokens = set(re.findall(r"\w+", target_topic.lower()))
        if not target_tokens:
            return 0.0
        source_tokens = set(re.findall(r"\w+", (s.title + " " + summary).lower()))
        return len(target_tokens & source_tokens)

    best_match = max(source_infos, key=lambda pair: _score(pair[0], pair[1]))[0]
    logger.info(
        f"Resolved source {best_match.id} via text matching for topic '{target_topic}'"
    )
    return best_match.id


async def get_prompt(
    client: RetryingNotebookLMClient | NotebookLMClient,
    inputs: Inputs,
    params: "AudioGenParams",
) -> str:
    duration = params.length

    source_id = inputs.source_id
    if not source_id:
        target_topic = inputs.focus or inputs.topic or ""
        source_id = await resolve_best_matching_source_id(
            client, params.notebook_id, target_topic
        )

    # Fetch source details from NotebookLM
    source = await client.sources.get(params.notebook_id, source_id)
    if not source:
        raise ValueError(
            f"Source {source_id} not found in notebook {params.notebook_id}"
        )

    # Fetch AI generated summary (source guide)
    guide = await client.sources.get_guide(params.notebook_id, source_id)
    topic_summary = guide.summary or source.title

    if inputs.roles and len(inputs.roles) == 2:
        host_role, guest_role = inputs.roles[0], inputs.roles[1]
        has_roles = True
    else:
        host_role, guest_role = "Host", "Guest"
        has_roles = False
    needs_inference = not (has_roles and inputs.agenda)
    category = "General"
    agenda = inputs.agenda

    if needs_inference:
        focus_clause = (
            f" with a specific focus on: '{inputs.focus}'" if inputs.focus else ""
        )
        prompt = (
            f"Based on the main source of this notebook ('{source.title}'){focus_clause}, I want to create a {duration} podcast. "
            "Please provide a JSON object with exactly three fields: "
            "'category' (e.g. Technology, Politics, Economy), "
            "'roles' (a list of exactly two strings: interviewer role and author/expert role, e.g. ['Tech Journalist', 'Lead Author']), and "
            "'agenda' (a sketch of the topics to cover, formatted as a brief list or paragraph). "
            "Respond ONLY with the JSON object, without markdown formatting."
        )
        logger.debug("Determining format arguments from NotebookLM...")
        try:
            data = await query_llm_json(
                client, params.notebook_id, prompt, source_ids=[source_id]
            )
            category = data.get("category", "General")
            if not has_roles:
                host_role, guest_role = resolve_two_roles(
                    data, fallback_host="Host", fallback_guest="Guest"
                )
            if not inputs.agenda:
                agenda = data.get("agenda")
        except Exception as exc:
            logger.debug(f"Failed to infer format args from chat: {exc}")

    template_path = Path(__file__).parent / "main_article_with_author.j2"
    return render_prompt_template(
        template_path,
        category=category,
        host_role=host_role,
        guest_role=guest_role,
        agenda=agenda,
        focus=inputs.focus,
        topic_title=source.title,
        topic_summary=topic_summary,
        length=duration,
    )
