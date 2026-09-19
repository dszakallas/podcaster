"""Cover image generation CLI commands."""

import click

from podcaster import cover as cover_module
from podcaster.config import DEFAULT_COVER_MODEL, load_config
from podcaster.models import CoverTask
from podcaster.utils.cli import async_command, verbose_option
from podcaster.utils.notebooklm import get_notebooklm_client

from . import cli
from .input import parse_input_stream


@cli.group(name="cover")
@verbose_option
def cover_group():
    """Manage podcast cover generation."""
    pass


@cover_group.command(name="create")
@click.argument("notebook_id")
@click.option(
    "--model",
    default=DEFAULT_COVER_MODEL,
    help="Model to use for cover generation.",
)
@verbose_option
@async_command()
async def cover_create(notebook_id, model):
    """Submit cover generation task. Outputs task JSON."""
    config = load_config()
    async with get_notebooklm_client(config.notebooklm) as client:
        return await cover_module.create_cover_job(notebook_id, client, model=model)


@cover_group.command(name="poll")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to poll.")
@verbose_option
@async_command(stream=True)
async def cover_poll(arg_json):
    """Poll cover generation tasks. Accepts input from --arg-json or stdin."""
    return cover_module.poll_cover_jobs(
        parse_input_stream(arg_json, model_cls=CoverTask)
    )


@cover_group.command(name="download")
@click.option(
    "--workdir",
    "-W",
    help="Output working directory (default: current directory)",
)
@click.option("--arg-json", multiple=True, help="JSON artifact object(s) to download.")
@verbose_option
@async_command(stream=True)
async def cover_download(workdir, arg_json):
    """Download cover generation results. Accepts input from --arg-json or stdin."""
    working_dir = workdir or "."
    return cover_module.download_cover_jobs(
        parse_input_stream(arg_json, model_cls=CoverTask), working_dir
    )
