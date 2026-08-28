"""Shared Click helpers for the root CLI and workflow plugins."""

import asyncio
import functools
import json
import logging
import sys

import click
from pydantic import BaseModel

from .logging import setup_logging


def _verbose_callback(ctx, param, value):
    root_ctx = ctx.find_root()
    is_verbose = bool(value) or bool(root_ctx.meta.get("verbose"))
    if is_verbose:
        root_ctx.meta["verbose"] = True
    setup_logging(verbose=is_verbose)
    return value


verbose_option = click.option(
    "--verbose",
    "-v",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    help="Enable verbose logging",
    callback=_verbose_callback,
)


def async_command(stream: bool = False):
    """Run an async Click command and render its JSON output."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            async def runner():
                try:
                    res = await fn(*args, **kwargs)
                    if stream and res is not None:
                        async for item in res:
                            if isinstance(item, BaseModel):
                                click.echo(item.model_dump_json())
                            else:
                                click.echo(json.dumps(item))
                            sys.stdout.flush()
                    elif res is not None:
                        if isinstance(res, BaseModel):
                            click.echo(res.model_dump_json())
                        elif isinstance(res, dict) and res.get("error"):
                            click.echo(json.dumps(res), err=True)
                            sys.exit(1)
                        else:
                            click.echo(json.dumps(res))
                        sys.stdout.flush()
                except Exception as exc:
                    logging.getLogger(__name__).debug("Error occurred", exc_info=True)
                    click.echo(json.dumps({"error": str(exc)}), err=True)
                    sys.exit(1)

            asyncio.run(runner())

        return wrapper

    return decorator
