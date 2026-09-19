"""Shared prompt construction, LLM response parsing, and templating for audio generation."""

import logging
from pathlib import Path
from typing import Any

import json_repair
from jinja2 import Environment

logger = logging.getLogger(__name__)

# Custom delimiters '${' and '}' are used specifically for prompt templates
# because prompt texts frequently contain curly braces that clash with default '{{ ... }}'.
PROMPT_TEMPLATE_ENV = Environment(
    variable_start_string="${",
    variable_end_string="}",
    autoescape=False,
)


def extract_json_payload(answer: str) -> dict[str, Any]:
    """Extract a dictionary from an LLM response, stripping markdown code fences if present."""
    text = answer.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    parsed = json_repair.loads(text.strip())
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object, got {type(parsed).__name__}")
    if not parsed:
        raise ValueError("LLM returned an empty JSON object")
    return parsed


async def query_llm_json(
    client: Any,
    notebook_id: str,
    prompt: str,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Query NotebookLM chat and parse the JSON response."""
    response = await client.chat.ask(
        notebook_id,
        prompt,
        source_ids=source_ids,
    )
    answer = getattr(response, "answer", response)
    return extract_json_payload(str(answer))


def resolve_two_roles(
    data: dict[str, Any],
    fallback_host: str = "Host",
    fallback_guest: str = "Co-host",
) -> tuple[str, str]:
    """Extract a pair of roles from an LLM response, supporting 'roles' list or individual fields."""
    inferred_roles = data.get("roles")
    if isinstance(inferred_roles, list) and len(inferred_roles) == 2:
        return str(inferred_roles[0]), str(inferred_roles[1])
    host = str(data.get("host_role") or fallback_host)
    guest = str(data.get("guest_role") or data.get("co_host_role") or fallback_guest)
    return host, guest


def render_prompt_template(template_path: Path | str, **context: Any) -> str:
    """Render a prompt template using the shared prompt environment."""
    path = Path(template_path)
    template = PROMPT_TEMPLATE_ENV.from_string(path.read_text(encoding="utf-8"))
    return template.render(**context)
