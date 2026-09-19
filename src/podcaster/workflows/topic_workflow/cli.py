"""Click command factory for the topic workflow."""

import logging
import uuid

import click
import dbos
import yaml

from podcaster.audio_gen.prompting import extract_json_payload
from podcaster.config import AppConfig, NotebookLMConfig
from podcaster.utils.cli import async_command, verbose_option
from podcaster.utils.dbos import shutdown_dbos, wait_for_workflow_result
from podcaster.utils.notebooklm import get_notebooklm_client

from ..common import WorkflowEnvironment
from .config import TopicWorkflowConfig
from .models import TopicWorkflowRecipe
from .workflow import TopicWorkflowOverrides, topic_workflow

logger = logging.getLogger(__name__)

RECIPE_INFERENCE_PROMPT = """You are an expert podcast producer and researcher. Based on the user's prompt, generate a JSON recipe for a topic-driven podcast workflow.

Output strictly valid JSON conforming to the following structure:
{
  "title": "A concise, descriptive title for the podcast series / notebook",
  "research": {
    "query": "A comprehensive web search query capturing key topics and terms from the request",
    "mode": "fast"
  },
  "podcasts": [
    {
      "type": "TopicDeepDive",
      "focus": "Specific focus, themes, or angle for this episode"
    },
    {
      "type": "TopicDebate",
      "focus": "The core disagreement or policy tension to be debated",
      "roles": ["Viewpoint or perspective of the first speaker", "Opposing viewpoint or perspective of the second speaker"]
    },
    {
      "type": "TopicArticle",
      "focus": "The specific article or subject for the author interview",
      "roles": ["Interviewer / journalist role", "Author / expert role"]
    }
  ]
}

Instructions:
1. Podcast Count and Coverage:
   - Carefully read the prompt to identify all distinct podcasts requested.
   - If the user asks for N podcasts (e.g. "Generate 3 podcasts: 1..., 2..., 3..."), the "podcasts" array MUST contain an entry for each requested podcast. Never combine or omit requested podcasts.
   - For each podcast, infer the appropriate "type" using exactly one of these names:
     * "TopicDeepDive": For overviews, deep dives, chronological summaries, comprehensive analyses, educational explorations, or single-perspective breakdowns.
     * "TopicDebate": For debates, discussions between opposing viewpoints, policy clashes, or discussions between conflicting sides.
     * "TopicArticle": For an in-depth interview with the author of a specific article, paper, or story discovered in the research.
2. Roles:
   - "roles": When specified, MUST be a list of exactly 2 strings describing the personas, stances, or perspectives of the two speakers.
   - For "TopicDebate" episodes, "roles" is REQUIRED with two contrasting stances (e.g. ["Pro-regulation US policymaker concerned with safety guardrails", "Pro-development policymaker emphasizing competitiveness"]).
   - For "TopicArticle" episodes, "roles" is optional/recommended (e.g. ["Tech Journalist", "Lead Author"]).
   - For "TopicDeepDive" episodes, "roles" is optional (e.g. ["Senior Tech Host", "AI Specialist"]).
3. Language Handling:
   - DO NOT include the "languages" field unless the user explicitly requested specific languages in their prompt.
   - When no language is explicitly requested by the user, omit "languages" entirely so that default system settings are used.
   - If the user explicitly asks for specific languages (e.g. "in English and Czech", "German episode"), provide the corresponding ISO 639-1 language codes (e.g. ["en", "cs"], ["de"]) on that specific podcast entry.
4. Length Handling:
   - DO NOT include the "length" field unless the user explicitly requested a specific duration or length (e.g. "short", "long", "default").
   - When not specified by the user, omit "length" entirely.
5. Research Query:
   - Formulate a clear, effective web search query capturing the core subject, key entities, and themes.
   - Default "mode" to "fast" unless the user explicitly requests deep, extensive, or comprehensive research.
6. JSON Validity:
   - ALL string values MUST be strictly valid JSON strings.
   - Do NOT copy backslash sequences (e.g. \\$, \\%, \\&) from the user's prompt into JSON string values. Write the literal character instead (e.g. "$" not "\\$").
   - The only valid JSON escape sequences inside strings are: \\", \\\\, \\/, \\b, \\f, \\n, \\r, \\t, \\uXXXX.

Respond ONLY with the JSON object. Do not include markdown code fences, comments, or introductory text."""


async def infer_recipe_from_prompt(
    prompt: str, notebooklm_config: NotebookLMConfig
) -> TopicWorkflowRecipe:
    """Infer a TopicWorkflowRecipe from natural language using NotebookLM chat."""
    async with get_notebooklm_client(notebooklm_config) as client:
        logger.info("Creating scratch notebook for recipe inference...")
        scratch_nb = await client.notebooks.create("Workflow Recipe Inference")
        nb_id = scratch_nb.id

        try:
            logger.info("Uploading user prompt as brief source...")
            source = await client.sources.add_text(
                nb_id,
                "User Prompt",
                prompt,
                wait=True,
            )

            logger.info("Prompting NotebookLM for structured workflow recipe...")
            chat_query = (
                f'User request:\n"""\n{prompt}\n"""\n\n{RECIPE_INFERENCE_PROMPT}'
            )
            chat_res = await client.chat.ask(
                nb_id,
                chat_query,
                source_ids=[source.id],
            )
            raw_answer = chat_res.answer

            data = extract_json_payload(raw_answer)
            return TopicWorkflowRecipe.model_validate(data)
        finally:
            logger.debug(f"Cleaning up scratch notebook {nb_id}...")
            try:
                await client.notebooks.delete(nb_id)
            except Exception as e:
                logger.warning(f"Failed to delete scratch notebook {nb_id}: {e}")


def create_command(
    preset_name: str,
    app_config: AppConfig,
    workflow_config: TopicWorkflowConfig,
) -> click.Command:
    """Create the command that runs one topic workflow preset."""

    @click.command(
        name=preset_name,
        help=f"Run '{preset_name}' (type: topic_workflow) workflow.",
    )
    @click.option(
        "--prompt",
        "-p",
        help="Natural language prompt describing the topic and desired podcasts",
    )
    @click.option(
        "--recipe",
        "-r",
        type=click.Path(exists=True),
        help="Path to YAML or JSON recipe file defining research and podcasts",
    )
    @click.option(
        "--recipe-json",
        help="Inline JSON string defining the topic workflow recipe",
    )
    @click.option(
        "--title",
        help="Override the podcast/notebook title",
    )
    @click.option(
        "--workflow-id",
        "-w",
        help="Explicit workflow run ID (auto-generated UUID if omitted)",
    )
    @click.option(
        "--enrich-web/--no-enrich-web",
        default=None,
        help="Enrich notebook with web research (default from config)",
    )
    @click.option(
        "--generate-cover/--no-generate-cover",
        default=None,
        help="Generate AI album cover (default from config)",
    )
    @click.option(
        "--transcribe/--no-transcribe",
        default=None,
        help="Transcribe podcast (default from config)",
    )
    @click.option(
        "--workdir",
        "-W",
        help="Workflow output root (default from config or current directory)",
    )
    @verbose_option
    @async_command()
    async def run_cmd(
        prompt: str | None,
        recipe: str | None,
        recipe_json: str | None,
        title: str | None,
        workflow_id: str | None,
        enrich_web: bool | None,
        generate_cover: bool | None,
        transcribe: bool | None,
        workdir: str | None,
    ):
        workdir = workdir or app_config.workflow.workdir
        wf_id = workflow_id or f"wf_{uuid.uuid4().hex[:12]}"

        parsed_recipe: TopicWorkflowRecipe
        if recipe:
            with open(recipe, encoding="utf-8") as f:
                raw_data = yaml.safe_load(f)
            parsed_recipe = TopicWorkflowRecipe.model_validate(raw_data)
        elif recipe_json:
            parsed_recipe = TopicWorkflowRecipe.model_validate_json(recipe_json)
        elif prompt:
            parsed_recipe = await infer_recipe_from_prompt(
                prompt, app_config.notebooklm
            )
        else:
            raise click.UsageError(
                "One of --prompt, --recipe, or --recipe-json must be provided."
            )

        if title:
            parsed_recipe = parsed_recipe.model_copy(update={"title": title})

        env = WorkflowEnvironment(
            workdir=workdir,
            workflow_id=wf_id,
            notebooklm_config=app_config.notebooklm,
            gcp_config=app_config.gcp,
        )
        overrides = TopicWorkflowOverrides(
            enrich_web=enrich_web,
            generate_cover=generate_cover,
            transcribe=transcribe,
        )

        try:
            handle = dbos.DBOS.start_workflow(
                topic_workflow,
                preset_name=preset_name,
                wf_config=workflow_config,
                recipe=parsed_recipe,
                env=env,
                overrides=overrides,
            )
            return await wait_for_workflow_result(handle.workflow_id)
        finally:
            shutdown_dbos()

    return run_cmd
