"""Notebook initialization CLI commands."""

import click

from podcaster import notebook
from podcaster.config import load_config
from podcaster.utils.cli import async_command, verbose_option
from podcaster.utils.notebooklm import get_notebooklm_client

from . import cli


@cli.command(name="init-podcast-notebook")
@click.option("--title", help="Title of the new notebook")
@click.option("--notebook-id", help="ID of an existing notebook to fetch")
@click.option("--from-source", help="Source file or URL to upload as the first source")
@click.option(
    "--importer",
    default="default",
    help="Importer to use for --from-source",
)
@verbose_option
@async_command()
async def init_podcast_notebook(title, notebook_id, from_source, importer):
    """Create a new notebook or fetch an existing one."""
    config = load_config()
    importer_cfg = config.importers.get(importer)
    if not importer_cfg:
        raise ValueError(f"Importer '{importer}' not found in configuration.")
    if from_source and notebook_id:
        raise ValueError("Cannot provide notebook-id when initializing from source.")
    if not from_source and not title and not notebook_id:
        raise ValueError(
            "Either --title or --notebook-id must be provided when not initializing from source."
        )

    async with get_notebooklm_client(config.notebooklm) as client:
        return await notebook.init_notebook(
            importer=importer_cfg,
            client=client,
            title=title,
            notebook_id=notebook_id,
            from_source=from_source,
        )
