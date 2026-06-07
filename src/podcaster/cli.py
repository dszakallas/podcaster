import asyncio
import click
import json
import sys
from .audio_gen import core as audio_gen_core
from . import tagging
from . import research
from . import plex
from .utils import setup_logging

@click.group()
def cli():
    """Podcaster automation tools."""
    pass

async def stream_stdin():
    """Helper to stream JSON objects from stdin."""
    loop = asyncio.get_event_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue

@cli.command(name="generate-podcast")
@click.argument('notebook_id')
@click.argument('type')
@click.option('--language', '-l', multiple=True, help='Target language (repeatable, default from config or en)')
@click.option('--length', type=click.Choice(['short', 'default', 'long']), help='Target length (default from config or long)')
@click.option('--format-args-json', help='JSON string with template arguments')
@click.option('--dry-run', is_flag=True, help='Skip actual generation')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
def generate_podcast(notebook_id, type, language, length, format_args_json, dry_run, verbose):
    """Generate podcasts using NotebookLM. Outputs newline-delimited JSON tasks."""
    setup_logging(verbose)
    async def run():
        async for task in audio_gen_core.generate_tasks(notebook_id, type, language, length, format_args_json, dry_run):
            click.echo(json.dumps(task))
            sys.stdout.flush()
    asyncio.run(run())

@cli.command(name="poll-artifact-task")
@click.option('--arg-json', multiple=True, help='JSON task object(s) to poll.')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
def poll_artifact_task(arg_json, verbose):
    """Poll audio generation tasks. Accepts input from --arg-json or stdin."""
    setup_logging(verbose)
    async def run():
        async def input_gen():
            if arg_json:
                for aj in arg_json:
                    try:
                        yield json.loads(aj)
                    except json.JSONDecodeError:
                        continue
            else:
                async for item in stream_stdin():
                    yield item

        async for completed in audio_gen_core.poll_tasks(input_gen()):
            click.echo(json.dumps(completed))
            sys.stdout.flush()
    asyncio.run(run())

@cli.command(name="download-podcast")
@click.option('--podcast-dir', '-p', help='Output directory (default from config or podcasts)')
@click.option('--arg-json', multiple=True, help='JSON artifact object(s) to download.')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
def download_podcast(podcast_dir, arg_json, verbose):
    """Download podcast artifacts. Accepts input from --arg-json or stdin."""
    setup_logging(verbose)
    async def run():
        async def input_gen():
            if arg_json:
                for aj in arg_json:
                    try:
                        yield json.loads(aj)
                    except json.JSONDecodeError:
                        continue
            else:
                async for item in stream_stdin():
                    yield item

        async for downloaded in audio_gen_core.download_artifacts(input_gen(), podcast_dir):
            click.echo(json.dumps(downloaded))
            sys.stdout.flush()
    asyncio.run(run())

@cli.command(name="tag-podcast")
@click.option('--cover', help='Path to cover image')
@click.option('--offset', default=0, help='Starting track number offset (default: 0)')
@click.option('--arg-json', multiple=True, help='JSON artifact object(s) to tag.')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
def tag_podcast(cover, offset, arg_json, verbose):
    """Tag podcast artifacts with metadata. Accepts input from --arg-json or stdin."""
    setup_logging(verbose)
    async def run():
        async def input_gen():
            if arg_json:
                for aj in arg_json:
                    try:
                        yield json.loads(aj)
                    except json.JSONDecodeError:
                        continue
            else:
                async for item in stream_stdin():
                    yield item

        async for tagged in audio_gen_core.tag_artifacts(input_gen(), cover, offset):
            click.echo(json.dumps(tagged))
            sys.stdout.flush()
    asyncio.run(run())

@cli.command(name="generate-cover")
@click.argument('notebook_id')
@click.option('--podcast-dir', '-p', help='Output directory (default from config or out)')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
def generate_cover(notebook_id, podcast_dir, verbose):
    """Generate an album cover for the podcast based on notebook summary."""
    setup_logging(verbose)
    async def run():
        try:
            path = await audio_gen_core.generate_cover(notebook_id, podcast_dir)
            import os
            click.echo(json.dumps({
                "notebook_id": notebook_id, 
                "filename": os.path.basename(path)
            }))
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("Error occurred", exc_info=True)
            click.echo(json.dumps({"error": str(e)}), err=True)
            sys.exit(1)
    asyncio.run(run())

@cli.command(name="research-from-source")
@click.argument('notebook_id')
@click.argument('source_id')
@click.option('--mode', type=click.Choice(['fast', 'deep']), default='fast', help='Research mode (default: fast)')
@click.option('--max-imports', type=int, help='Maximum number of sources to import')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
def research_from_source(notebook_id, source_id, mode, max_imports, verbose):
    """Enrich a notebook with research based on a source guide and summary."""
    setup_logging(verbose)
    async def run():
        try:
            res = await research.research_from_source(notebook_id, source_id, mode, max_imports)
            click.echo(json.dumps(res))
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("Error occurred", exc_info=True)
            click.echo(json.dumps({"error": str(e)}), err=True)
            sys.exit(1)
    asyncio.run(run())

@cli.command(name="sync-podcast-to-plex")
@click.argument('notebook_id')
@click.argument('plex_section_id')
@click.option('--podcast-dir', '-p', help='Base directory where podcasts are stored (default from config or out)')
@click.option('--server-url', envvar='PLEX_SERVER_URL', help='Plex Server URL (e.g. http://localhost:32400)')
@click.option('--token', envvar='PLEX_TOKEN', help='Plex Authentication Token')
@click.option('--server-library-path', envvar='PLEX_SERVER_LIBRARY_PATH', help='Remote library path on the Plex server')
@click.option('--grace-period', type=int, default=30, help='Seconds to wait for cloud sync (default: 30)')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
def sync_podcast_to_plex(notebook_id, plex_section_id, podcast_dir, server_url, token, server_library_path, grace_period, verbose):
    """Sync a notebook's podcasts to a Plex library."""
    setup_logging(verbose)
    async def run():
        try:
            res = await plex.sync_to_plex(notebook_id, plex_section_id, podcast_dir, server_url, token, server_library_path, grace_period)
            click.echo(json.dumps(res))
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("Error occurred", exc_info=True)
            click.echo(json.dumps({"error": str(e)}), err=True)
            sys.exit(1)
    asyncio.run(run())


@cli.command(name="init-podcast-notebook")
@click.argument('title')
@click.option('--podcast-dir', help='Root podcast directory (default from config or podcasts)')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
def init_podcast_notebook(title, podcast_dir, verbose):
    """Create a new notebook and its local directory."""
    setup_logging(verbose)
    async def run():
        res = await audio_gen_core.init_notebook(title, podcast_dir)
        click.echo(json.dumps(res))
    asyncio.run(run())

@click.group()
def workflow():
    """Higher-level podcast workflows."""
    pass

@workflow.command(name="deep-dive-single-article")
@click.argument('title')
@click.argument('source_file', type=click.Path(exists=True))
@click.option('--length', type=click.Choice(['short', 'default', 'long']), help='Target length (default from config)')
@click.option('--language', '-l', multiple=True, help='Target language (repeatable, default from config)')
@click.option('--enrich-sources/--no-enrich-sources', default=None, help='Enrich notebook with web research (default: True)')
@click.option('--generate-cover/--no-generate-cover', default=None, help='Generate AI album cover (default: True)')
@click.option('--sync-plex/--no-sync-plex', default=None, help='Sync to Plex (default: True)')
@click.option('--podcast-dir', '-p', help='Base directory for storage (default from config)')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
def deep_dive_single_article(title, source_file, length, language, enrich_sources, generate_cover, sync_plex, podcast_dir, verbose):
    """Full automated workflow to create a podcast from a single source file."""
    setup_logging(verbose)
    
    async def run():
        from .workflows.deep_dive_single_article import workflow as dd_wf
        res = await dd_wf.run(
            title, 
            source_file=source_file, 
            length=length, 
            languages=list(language) if language else None,
            enrich_sources=enrich_sources,
            generate_cover=generate_cover,
            sync_plex=sync_plex,
            podcast_dir=podcast_dir,
            verbose=verbose
        )
        click.echo(json.dumps(res))
    asyncio.run(run())

cli.add_command(workflow)

if __name__ == "__main__":
    cli()
