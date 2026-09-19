"""Research enrichment CLI commands."""

import click

from podcaster import research
from podcaster.config import load_config
from podcaster.models import ResearchTask
from podcaster.utils.cli import async_command, verbose_option
from podcaster.utils.notebooklm import get_notebooklm_client

from . import cli
from .input import parse_input_stream


@cli.group(name="research")
@verbose_option
def research_group():
    """Manage web research and source enrichment."""
    pass


@research_group.command(name="create")
@click.argument("notebook_id")
@click.argument("source_id")
@click.option(
    "--mode",
    type=click.Choice(["fast", "deep"]),
    default="fast",
    help="Research mode (default: fast)",
)
@verbose_option
@async_command()
async def research_create(notebook_id, source_id, mode):
    """Enrich a notebook with research based on a source guide and summary. Outputs task JSON."""
    config = load_config()
    async with get_notebooklm_client(config.notebooklm) as client:
        return await research.create_research_job(
            notebook_id, source_id, client=client, mode=mode
        )


@research_group.command(name="poll")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to poll.")
@click.option(
    "--fallback-importer",
    help="Importer preset name to use on failure",
)
@click.option(
    "--max-import-failures",
    type=int,
    help="Maximum allowed import failures before failing the research job",
)
@verbose_option
@async_command(stream=True)
async def research_poll(arg_json, fallback_importer, max_import_failures):
    """Poll research tasks and import sources. Accepts input from --arg-json or stdin."""
    config = load_config()
    fallback_cfg = None
    if fallback_importer:
        if fallback_importer in config.importers:
            fallback_cfg = config.importers[fallback_importer]
        else:
            raise ValueError(
                f"Importer '{fallback_importer}' not found in configuration."
            )
    return research.poll_research_jobs(
        parse_input_stream(arg_json, model_cls=ResearchTask),
        config.notebooklm,
        fallback_importer=fallback_cfg,
        max_import_failures=max_import_failures,
    )
