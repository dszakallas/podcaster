"""Transcription CLI commands."""

import click

from podcaster import transcription
from podcaster.config import load_config
from podcaster.models import PodcastGenArtifact, TranscriptionTask
from podcaster.utils.cli import async_command, verbose_option

from . import cli
from .input import parse_input_stream


@cli.group(name="transcription")
@verbose_option
def transcription_group():
    """Manage speech transcription."""
    pass


@transcription_group.command(name="create")
@click.option("--arg-json", multiple=True, help="JSON artifact object(s) to process.")
@click.option(
    "--transcriber-key", default="default", help="Key for transcriber preset config."
)
@verbose_option
@async_command(stream=True)
async def transcription_create(arg_json, transcriber_key):
    """Start transcription tasks for podcast artifacts. Accepts input from --arg-json or stdin."""
    config = load_config()
    trans_cfg = config.podcast_transcribers.get(transcriber_key)
    if not trans_cfg:
        raise ValueError(
            f"Transcriber preset '{transcriber_key}' not found in configuration"
        )
    return transcription.create_transcription_jobs(
        parse_input_stream(arg_json, model_cls=PodcastGenArtifact),
        gcp_config=config.gcp,
        transcription_config=trans_cfg,
    )


@transcription_group.command(name="poll")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to poll.")
@verbose_option
@async_command(stream=True)
async def transcription_poll(arg_json):
    """Poll speech recognition batch jobs. Accepts input from --arg-json or stdin."""
    config = load_config()
    return transcription.poll_transcription_jobs(
        parse_input_stream(arg_json, model_cls=TranscriptionTask),
        gcp_config=config.gcp,
    )


@transcription_group.command(name="download")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to download.")
@verbose_option
@async_command(stream=True)
async def transcription_download(arg_json):
    """Download transcription result and write JSON and LRC files. Accepts input from --arg-json or stdin."""
    config = load_config()
    return transcription.download_transcription_jobs(
        parse_input_stream(arg_json, model_cls=TranscriptionTask),
        gcp_config=config.gcp,
    )
