"""Tagging and local podcast listing CLI commands."""

import json
import logging
import os
import re
import sys

import click

from podcaster import tagging
from podcaster.config import load_config
from podcaster.models import PodcastGenArtifact
from podcaster.utils.cli import async_command, verbose_option

from . import cli
from .input import parse_input_stream


@cli.command(name="tag-podcast")
@click.option("--cover", help="Path to cover image")
@click.option("--offset", default=0, help="Starting track number offset (default: 0)")
@click.option("--album", help="Album name for metadata tagging")
@click.option("--date", help="Recording date for metadata tagging")
@click.option("--arg-json", multiple=True, help="JSON artifact object(s) to tag.")
@click.option("--preset", required=True, help="Podcast tag preset to apply")
@verbose_option
@async_command(stream=True)
async def tag_podcast(cover, offset, album, date, arg_json, preset):
    """Tag podcast artifacts with metadata. Accepts input from --arg-json or stdin."""
    config = load_config()
    tags_config = config.podcast_tags.get(preset)
    if not tags_config:
        raise ValueError(f"Podcast tag preset '{preset}' not found in configuration.")
    return tagging.tag_artifacts(
        parse_input_stream(arg_json, model_cls=PodcastGenArtifact),
        cover,
        offset,
        album=album,
        created_at=date,
        tags_config=tags_config,
    )


@cli.command(name="list-podcasts")
@click.option(
    "--workdir",
    "-W",
    default=".",
    help="Directory to search (default: current directory)",
)
@verbose_option
def list_podcasts(workdir):
    """List locally available podcasts and their notebook IDs."""
    if not os.path.exists(workdir):
        click.echo(json.dumps({"error": f"Directory not found: {workdir}"}), err=True)
        sys.exit(1)

    try:
        for item in os.listdir(workdir):
            item_path = os.path.join(workdir, item)
            if os.path.isdir(item_path):
                # Pattern matches "[nlm_...]" at the end of the folder name
                match = re.search(r"\[nlm_([a-zA-Z0-9-]+)\]$", item)
                if match:
                    notebook_id = match.group(1)
                    title = item[: match.start()].strip()
                    click.echo(
                        json.dumps(
                            {
                                "notebook_id": notebook_id,
                                "title": title,
                                "local_dir": item_path,
                            }
                        )
                    )
    except Exception as e:
        logging.getLogger(__name__).debug("Error occurred", exc_info=True)
        click.echo(json.dumps({"error": str(e)}), err=True)
        sys.exit(1)
