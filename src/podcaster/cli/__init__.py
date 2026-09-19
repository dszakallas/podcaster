"""Podcaster Click CLI entry point."""

import importlib

import click

from podcaster.utils.cli import verbose_option

from .input import parse_input_stream, stream_stdin


@click.group()
@verbose_option
def cli():
    """Podcaster automation tools."""
    pass


# Load submodules to register commands and groups on ``cli``.
for _submod in (
    "cover",
    "distribute",
    "importing",
    "notebook",
    "podcast",
    "research",
    "tagging",
    "transcription",
    "workflow",
):
    importlib.import_module(f"{__name__}.{_submod}")

__all__ = ["cli", "parse_input_stream", "stream_stdin"]
