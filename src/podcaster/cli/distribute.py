"""Distribution CLI commands."""

import json
import logging
import os

import click

from podcaster.config import load_config
from podcaster.utils.cli import async_command, verbose_option

from . import cli


@cli.command(name="distribute")
@click.option(
    "--workdir",
    "-W",
    help="Working directory containing podcast files to distribute",
)
@click.option(
    "--preset",
    default="default",
    show_default=True,
    help="Named distribution preset from config",
)
@click.option(
    "--flag",
    "flag",
    multiple=True,
    help="Additional flags to pass to rsync/rclone (can be specified multiple times)",
)
@verbose_option
@async_command()
async def distribute(
    workdir,
    preset,
    flag,
):
    """Distribute podcasts from working directory using a named distribution preset."""
    config = load_config()
    working_dir = workdir or "."

    if preset not in config.distributions:
        raise ValueError(f"Distribution preset '{preset}' not found in configuration.")

    from podcaster.distribution import build_distribution

    dist_cfg = config.distributions[preset]
    if flag:
        if hasattr(dist_cfg, "model_copy"):
            dist_cfg = dist_cfg.model_copy(deep=True)
        rsync_cfg = getattr(dist_cfg, "rsync", None)
        if rsync_cfg is not None:
            rsync_cfg.flags = [*rsync_cfg.flags, *flag]

    dist_obj = build_distribution(dist_cfg, name=preset)

    metadata = None
    meta_path = os.path.join(working_dir, "metadata.json")
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                metadata = json.load(f)
        except Exception as e:
            logging.getLogger(__name__).warning("Failed to load %s: %s", meta_path, e)

    return await dist_obj.distribute(working_dir=working_dir, metadata=metadata)
