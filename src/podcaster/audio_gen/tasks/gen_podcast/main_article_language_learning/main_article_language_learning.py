import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from jinja2 import Environment
from notebooklm import NotebookLMClient
from pydantic import BaseModel

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ....params import AudioGenParams


class Inputs(BaseModel):
    source_id: str
    target_level: Optional[str] = "B2"  # A1, A2, B1, B2
    delivery_speed: Optional[str] = "normal"  # normal, slow, slower
    segmented_summaries: Optional[str] = "off"  # off, moderate, frequent


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
    topic_summary = getattr(guide, "summary", None)

    if not topic_summary:
        raise RuntimeError(
            f"Could not retrieve topic summary for source {inputs.source_id}"
        )

    summary_instruction = ""
    if inputs.segmented_summaries == "moderate":
        summary_instruction = " Please include a 'recap' topic every 2-3 main topics (roughly every 7-10 minutes) where Host 1 summarizes the discussion so far in simpler terms."
    elif inputs.segmented_summaries == "frequent":
        summary_instruction = " Please include a 'recap' topic after every main topic (roughly every 3-6 minutes) where Host 1 summarizes the discussion so far in simpler terms."

    # Ask NotebookLM for roles and category
    prompt = (
        f"Based on the main source of this notebook, I want to create a {duration} podcast optimized for language learners at the {inputs.target_level} level. "
        "Please provide a JSON object with exactly four string fields: "
        "'category' (e.g. Technology, Politics, Economy), "
        "'host_role' (e.g. Language Teacher, Explainer, Enthusiastic Learner), "
        "'guest_role' (e.g. Expert, Native Speaker, Interviewee), and "
        "'agenda' (a sketch of the topics to cover, formatted as a brief list or paragraph, focused on explaining the source material clearly). "
        "When defining the roles and agenda, keep in mind that the host's main role is to actively engage in a conversation and to facilitate the learning process for the target level. "
        f"{summary_instruction} "
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
        category = data.get("category", "Language Learning")
        host_role = data.get("host_role", "Teacher")
        guest_role = data.get("guest_role", "Expert")
        agenda = data.get("agenda", None)
    except json.JSONDecodeError:
        logger.debug(f"Failed to parse format args JSON: {answer}")
        category = "Language Learning"
        host_role = "Teacher"
        guest_role = "Expert"
        agenda = None

    template_path = Path(__file__).parent / "main_article_language_learning.j2"
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
        target_level=inputs.target_level,
        delivery_speed=inputs.delivery_speed,
        segmented_summaries=inputs.segmented_summaries,
    )
