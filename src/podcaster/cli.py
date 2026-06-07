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
@click.option('--podcast-dir', '-p', help='Output directory (default from config or out)')
@click.option('--cover', help='Path to cover image')
@click.option('--arg-json', multiple=True, help='JSON artifact object(s) to download.')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
def download_podcast(podcast_dir, cover, arg_json, verbose):
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

        async for downloaded in audio_gen_core.download_artifacts(input_gen(), podcast_dir, cover):
            click.echo(json.dumps(downloaded))
            sys.stdout.flush()
    asyncio.run(run())

@cli.command(name="gen-cover")
@click.argument('notebook_id')
@click.option('--podcast-dir', '-p', help='Output directory (default from config or out)')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
def gen_cover(notebook_id, podcast_dir, verbose):
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

@cli.command(name="create-podcast")
@click.argument('title')
@click.option('--source-file', required=True, help='Path to the source file to upload')
@click.option('--length', type=click.Choice(['short', 'default', 'long']), help='Target length (default from config)')
@click.option('--language', '-l', multiple=True, help='Target language (repeatable, default from config)')
@click.option('--enrich-sources/--no-enrich-sources', default=True, help='Enrich notebook with web research (default: True)')
@click.option('--gen-cover/--no-gen-cover', default=True, help='Generate AI album cover (default: True)')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
def create_podcast(title, source_file, length, language, enrich_sources, gen_cover, verbose):
    """Full automated workflow to create a podcast from a source file."""
    setup_logging(verbose)
    async def run():
        from .workflow import run_workflow
        res = await run_workflow(
            title, 
            source_file, 
            length, 
            list(language) if language else None,
            enrich_sources=enrich_sources,
            gen_cover=gen_cover
        )
        click.echo(json.dumps(res))
    asyncio.run(run())

if __name__ == "__main__":
    cli()
