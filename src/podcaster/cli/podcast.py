"""Podcast audio generation CLI commands."""

import json
from typing import Any

import click

from podcaster.audio_gen import core as audio_gen
from podcaster.config import load_config
from podcaster.models import PodcastGenTask
from podcaster.utils.cli import async_command, verbose_option

from . import cli
from .input import parse_input_stream


@cli.group(name="podcast")
@verbose_option
def podcast_group():
    """Manage podcast generation."""
    pass


@podcast_group.command(name="create")
@click.argument("notebook_id")
@click.argument("type")
@click.option(
    "-l",
    "language",
    multiple=True,
    help="Target language (repeatable, default from config or en)",
)
@click.option(
    "--length",
    type=click.Choice(["short", "default", "long", "auto"]),
    help="Target length (default from config or long)",
)
@click.option(
    "--generator-key",
    default="default",
    help="Podcast generator key from configuration (default: default)",
)
@click.option("--format-args-json", help="JSON string with template arguments")
@click.option("--dry-run", is_flag=True, help="Skip actual generation")
@verbose_option
@async_command(stream=True)
async def podcast_create(
    notebook_id,
    type,
    language,
    length,
    generator_key,
    format_args_json,
    dry_run,
):
    """Generate podcasts using NotebookLM. Outputs task JSON."""
    config = load_config()
    gen_cfg = config.podcast_generators.get(generator_key)
    if not gen_cfg:
        raise ValueError(
            f"Generator preset '{generator_key}' not found in configuration"
        )
    format_args: dict[str, Any] = (
        json.loads(format_args_json) if format_args_json else {}
    )
    return audio_gen.create_podcast_audio_jobs(
        notebook_id,
        type,
        [lang.lower() for lang in language] if language else [],
        length,
        format_args,
        generator_config=gen_cfg,
        notebooklm_config=config.notebooklm,
        dry_run=dry_run,
    )


@podcast_group.command(name="poll")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to poll.")
@verbose_option
@async_command(stream=True)
async def podcast_poll(arg_json):
    """Poll audio generation tasks. Accepts input from --arg-json or stdin."""
    config = load_config()
    return audio_gen.poll_tasks(
        parse_input_stream(arg_json, model_cls=PodcastGenTask), config.notebooklm
    )


@podcast_group.command(name="download")
@click.option(
    "--workdir",
    "-W",
    help="Output working directory (default: current directory)",
)
@click.option("--arg-json", multiple=True, help="JSON artifact object(s) to download.")
@verbose_option
@async_command(stream=True)
async def podcast_download(workdir, arg_json):
    """Download podcast artifacts. Accepts input from --arg-json or stdin."""
    config = load_config()
    working_dir = workdir or "."
    return audio_gen.download_artifacts(
        parse_input_stream(arg_json, model_cls=PodcastGenTask),
        working_dir,
        config.notebooklm,
    )
