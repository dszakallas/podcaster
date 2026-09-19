import logging
from pathlib import Path
from typing import TYPE_CHECKING

from notebooklm import AudioFormat, NotebookLMClient

from podcaster.audio_gen.prompting import (
    query_llm_json,
    render_prompt_template,
    resolve_two_roles,
)

from ....params import StandardPodcastTaskInputs

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ....params import AudioGenParams

AUDIO_FORMAT = AudioFormat.DEEP_DIVE


class Inputs(StandardPodcastTaskInputs):
    pass


async def get_prompt(
    client: NotebookLMClient, inputs: Inputs, params: "AudioGenParams"
) -> str:
    duration = params.length
    topic = inputs.topic or "Topic Overview"
    focus_clause = (
        f" with a specific focus on: '{inputs.focus}'" if inputs.focus else ""
    )

    if inputs.roles and len(inputs.roles) == 2:
        host_role, co_host_role = inputs.roles[0], inputs.roles[1]
        has_roles = True
    else:
        host_role, co_host_role = "Host", "Co-host"
        has_roles = False
    needs_inference = not (has_roles and inputs.agenda)
    category = "General"
    agenda = inputs.agenda

    if needs_inference:
        prompt = (
            f"Based on the sources in this notebook regarding the topic '{topic}'{focus_clause}, "
            f"I want to create a {duration} deep dive podcast overview. "
            "Please provide a JSON object with exactly three fields: "
            "'category' (e.g. Technology, Science, Economy, Politics), "
            "'roles' (a list of exactly two strings describing the distinct host roles/perspectives, e.g. ['Investigative Journalist', 'Domain Expert']), and "
            "'agenda' (a sketch of key topics and connections to cover, formatted as a brief list or paragraph). "
            "Respond ONLY with the JSON object, without markdown formatting."
        )
        logger.debug("Determining format arguments from NotebookLM chat...")
        try:
            data = await query_llm_json(client, params.notebook_id, prompt)
            category = data.get("category", "General")
            if not has_roles:
                host_role, co_host_role = resolve_two_roles(
                    data, fallback_host="Host", fallback_guest="Co-host"
                )
            if not inputs.agenda:
                agenda = data.get("agenda")
        except Exception as exc:
            logger.debug(f"Failed to parse format args JSON from NotebookLM: {exc}")

    template_path = Path(__file__).parent / "topic_deep_dive.j2"
    return render_prompt_template(
        template_path,
        category=category,
        host_role=host_role,
        co_host_role=co_host_role,
        agenda=agenda,
        topic=topic,
        focus=inputs.focus,
        length=duration,
    )
