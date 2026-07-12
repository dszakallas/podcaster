import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment
from notebooklm import NotebookLMClient
from pydantic import BaseModel

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ....params import AudioGenParams


class Inputs(BaseModel):
    source_id: str


async def get_prompt(
    client: NotebookLMClient, inputs: Inputs, params: "AudioGenParams"
) -> str:
    duration = params.length

    # Fetch source details from NotebookLM
    source = await client.sources.get(params.notebook_id, inputs.source_id)
    if not source:
        raise ValueError(
            f"Source {inputs.source_id} not found in notebook {params.notebook_id}"
        )

    # Fetch AI generated summary (source guide)
    guide = await client.sources.get_guide(params.notebook_id, inputs.source_id)
    topic_summary = guide.get("summary")

    if not topic_summary:
        raise RuntimeError(
            f"Could not retrieve topic summary for source {inputs.source_id}"
        )

    # Ask NotebookLM for roles and category
    prompt = (
        f"Based on the main source of this notebook, I want to create a {duration} podcast. "
        "Please provide a JSON object with exactly four string fields: "
        "'category' (e.g. Technology, Politics, Economy), "
        "'host_role' (e.g. Tech Journalist, Political Analyst, Storyteller), "
        "'guest_role' (e.g. Security Expert, Author, Subject Matter Expert), and "
        "'agenda' (a sketch of the topics to cover, formatted as a brief list or paragraph). "
        "Respond ONLY with the JSON object, without markdown formatting."
    )
    logger.debug("Determining format arguments from NotebookLM...")
    response = await client.chat.ask(
        params.notebook_id, prompt, source_ids=[inputs.source_id]
    )
    answer = response.answer.strip()

    # Clean up possible markdown wrappers
    if answer.startswith("```json"):
        answer = answer[7:]
    elif answer.startswith("```"):
        answer = answer[3:]
    if answer.endswith("```"):
        answer = answer[:-3]

    try:
        data = json.loads(answer.strip())
        category = data.get("category", "General")
        host_role = data.get("host_role", "Host")
        guest_role = data.get("guest_role", "Guest")
        agenda = data.get("agenda", None)
    except json.JSONDecodeError:
        logger.debug(f"Failed to parse format args JSON: {answer}")
        category = "General"
        host_role = "Host"
        guest_role = "Guest"
        agenda = None

    template_path = Path(__file__).parent / "main_article_with_author.j2"
    env = Environment(variable_start_string="${", variable_end_string="}")
    template = env.from_string(template_path.read_text())

    return template.render(
        category=category,
        host_role=host_role,
        guest_role=guest_role,
        agenda=agenda,
        topic_title=source.title,
        topic_summary=topic_summary,
        length=duration,
    )
