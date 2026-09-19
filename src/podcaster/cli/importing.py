"""Source importing and scraping CLI commands."""

import click

from podcaster import research
from podcaster.config import load_config
from podcaster.utils.cli import async_command, verbose_option
from podcaster.utils.notebooklm import get_notebooklm_client

from . import cli


@cli.command(name="import-web")
@click.argument("notebook_id")
@click.argument("url")
@click.option(
    "--importer",
    default="default",
    help="Importer to use for URL import",
)
@click.option("--title", help="Title for the imported source")
@verbose_option
@async_command()
async def import_web(notebook_id, url, importer, title):
    """Import a web URL as a source to a notebook."""
    config = load_config()
    importer_cfg = config.importers.get(importer)
    if not importer_cfg:
        raise ValueError(f"Importer '{importer}' not found in configuration.")
    async with get_notebooklm_client(config.notebooklm) as client:
        return await research.execute_importer(
            importer=importer_cfg,
            client=client,
            notebook_id=notebook_id,
            source=url,
            title=title,
        )


@cli.command(name="import-drive")
@click.argument("notebook_id")
@click.argument("url_or_id")
@click.option("--title", help="Title for the imported Drive document")
@click.option(
    "--importer",
    default="default",
    help="Importer to use",
)
@verbose_option
@async_command()
async def import_drive(notebook_id, url_or_id, title, importer):
    """Import a Google Drive document URL or ID as a source to a notebook."""
    if not url_or_id.startswith("http"):
        url = f"https://docs.google.com/document/d/{url_or_id}/edit"
    else:
        url = url_or_id

    config = load_config()
    importer_cfg = config.importers.get(importer)
    if not importer_cfg:
        raise ValueError(f"Importer '{importer}' not found in configuration.")

    async with get_notebooklm_client(config.notebooklm) as client:
        return await research.execute_importer(
            importer=importer_cfg,
            client=client,
            notebook_id=notebook_id,
            source=url,
            title=title,
        )


@cli.command(name="scrape")
@click.argument("target")
@click.option("--scraper", "scraper_name", required=True, help="Scraper preset to use")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Do not execute the scraper, only log the command that would run",
)
@click.option(
    "--timeout",
    type=float,
    default=None,
    help="Optional timeout in seconds for the scraper process",
)
@verbose_option
@async_command()
async def scrape_cmd(target, scraper_name, dry_run, timeout):
    """Scrape a target URL and output the result with metadata on a single NDJSON line."""
    if timeout is not None and timeout <= 0:
        raise ValueError(f"Timeout must be positive, got {timeout}")
    config = load_config()
    scraper_cfg = config.scrapers.get(scraper_name)
    if not scraper_cfg:
        raise ValueError(f"Scraper '{scraper_name}' not found in configuration.")
    return await research.scrape_source(
        target, dry_run=dry_run, scraper_config=scraper_cfg, timeout=timeout
    )
