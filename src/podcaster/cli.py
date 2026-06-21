import asyncio
import json
import sys

import click

from . import cover, notebook, plex, research, tagging, transcription
from .audio_gen import core as audio_gen_core
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


@cli.group(name="podcast")
def podcast_group():
    """Manage podcast generation."""
    pass


@podcast_group.command(name="create")
@click.argument("notebook_id")
@click.argument("type")
@click.option(
    "--language",
    "-l",
    multiple=True,
    help="Target language (repeatable, default from config or en)",
)
@click.option(
    "--length",
    type=click.Choice(["short", "default", "long", "auto"]),
    help="Target length (default from config or long)",
)
@click.option("--format-args-json", help="JSON string with template arguments")
@click.option("--dry-run", is_flag=True, help="Skip actual generation")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def podcast_create(
    notebook_id, type, language, length, format_args_json, dry_run, verbose
):
    """Generate podcasts using NotebookLM. Outputs task JSON."""
    setup_logging(verbose)

    async def run():
        async for task in audio_gen_core.generate_tasks(
            notebook_id, type, language, length, format_args_json, dry_run
        ):
            click.echo(json.dumps(task))
            sys.stdout.flush()

    asyncio.run(run())


@podcast_group.command(name="poll")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to poll.")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def podcast_poll(arg_json, verbose):
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


@podcast_group.command(name="download")
@click.option(
    "--podcast-dir", "-p", help="Output directory (default from config or podcasts)"
)
@click.option("--arg-json", multiple=True, help="JSON artifact object(s) to download.")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def podcast_download(podcast_dir, arg_json, verbose):
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

        async for downloaded in audio_gen_core.download_artifacts(
            input_gen(), podcast_dir
        ):
            click.echo(json.dumps(downloaded))
            sys.stdout.flush()

    asyncio.run(run())


@cli.group(name="cover")
def cover_group():
    """Manage podcast cover generation."""
    pass


@cover_group.command(name="create")
@click.argument("notebook_id")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def cover_create(notebook_id, verbose):
    """Submit cover generation task. Outputs task JSON."""
    setup_logging(verbose)

    async def run():
        try:
            task = await cover.create_cover_job(notebook_id)
            click.echo(json.dumps(task))
            sys.stdout.flush()
        except Exception as e:
            import logging

            logging.getLogger(__name__).debug("Error occurred", exc_info=True)
            click.echo(json.dumps({"error": str(e)}), err=True)
            sys.exit(1)

    asyncio.run(run())


@cover_group.command(name="poll")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to poll.")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def cover_poll(arg_json, verbose):
    """Poll cover generation tasks. Accepts input from --arg-json or stdin."""
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

        async for completed in cover.poll_cover_jobs(input_gen()):
            click.echo(json.dumps(completed))
            sys.stdout.flush()

    asyncio.run(run())


@cover_group.command(name="download")
@click.option(
    "--podcast-dir", "-p", help="Output directory (default from config or podcasts)"
)
@click.option("--arg-json", multiple=True, help="JSON task object(s) to download.")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def cover_download(podcast_dir, arg_json, verbose):
    """Download cover generation results. Accepts input from --arg-json or stdin."""
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

        async for downloaded in cover.download_cover_jobs(input_gen(), podcast_dir):
            click.echo(json.dumps(downloaded))
            sys.stdout.flush()

    asyncio.run(run())


@cli.group(name="transcription")
def transcription_group():
    """Manage speech transcription."""
    pass


@transcription_group.command(name="create")
@click.option(
    "--arg-json", multiple=True, help="JSON artifact object(s) to transcribe."
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def transcription_create(arg_json, verbose):
    """Start transcription tasks for podcast artifacts. Accepts input from --arg-json or stdin."""
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

        async for task in transcription.create_transcription_jobs(input_gen(), verbose):
            click.echo(json.dumps(task))
            sys.stdout.flush()

    asyncio.run(run())


@transcription_group.command(name="poll")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to poll.")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def transcription_poll(arg_json, verbose):
    """Poll speech recognition batch jobs. Accepts input from --arg-json or stdin."""
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

        async for completed in transcription.poll_transcription_jobs(input_gen()):
            click.echo(json.dumps(completed))
            sys.stdout.flush()

    asyncio.run(run())


@transcription_group.command(name="download")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to download.")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def transcription_download(arg_json, verbose):
    """Download transcription result and write JSON and LRC files. Accepts input from --arg-json or stdin."""
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

        async for downloaded in transcription.download_transcription_jobs(
            input_gen(), verbose
        ):
            click.echo(json.dumps(downloaded))
            sys.stdout.flush()

    asyncio.run(run())


@cli.command(name="tag-podcast")
@click.option("--cover", help="Path to cover image")
@click.option("--offset", default=0, help="Starting track number offset (default: 0)")
@click.option("--arg-json", multiple=True, help="JSON artifact object(s) to tag.")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
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

        async for tagged in tagging.tag_artifacts(input_gen(), cover, offset):
            click.echo(json.dumps(tagged))
            sys.stdout.flush()

    asyncio.run(run())


@cli.command(name="list-podcasts")
@click.option(
    "--podcast-dir", "-p", help="Base directory for storage (default from config)"
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def list_podcasts(podcast_dir, verbose):
    """List locally available podcasts and their notebook IDs."""
    setup_logging(verbose)

    import os
    import re

    from .utils import load_config

    config = load_config()
    if not podcast_dir:
        podcast_dir = config.podcast_dir

    if not os.path.exists(podcast_dir):
        click.echo(
            json.dumps({"error": f"Directory not found: {podcast_dir}"}), err=True
        )
        sys.exit(1)

    try:
        for item in os.listdir(podcast_dir):
            item_path = os.path.join(podcast_dir, item)
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
        import logging

        logging.getLogger(__name__).debug("Error occurred", exc_info=True)
        click.echo(json.dumps({"error": str(e)}), err=True)
        sys.exit(1)


@cli.command(name="import-web")
@click.argument("notebook_id")
@click.argument("url")
@click.option(
    "--unimportables",
    help="Path to a file with newline delimited regexes for unimportable sites",
)
@click.option(
    "--import-fallback",
    type=click.Choice(["ignore", "force", "scrape"]),
    help="Fallback method for unimportable sites (default: from config or scrape)",
)
@click.option(
    "--tool",
    "-t",
    help="The MCP scraping tool to use (e.g. playwright, chrome-devtools)",
)
@click.option("--command", help="The agent harness command (overrides config)")
@click.option(
    "--agent-arg",
    multiple=True,
    help="Arguments for the agent command (overrides config)",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def import_web(
    notebook_id, url, unimportables, import_fallback, tool, command, agent_arg, verbose
):
    """Import a web URL as a source to a notebook."""
    setup_logging(verbose)

    async def run():
        try:
            import re

            patterns = None
            if unimportables:
                with open(unimportables, "r") as f:
                    patterns = [
                        re.compile(line.strip(), re.IGNORECASE)
                        for line in f
                        if line.strip()
                    ]

            res = await research.import_web_source(
                notebook_id,
                url,
                unimportables=patterns,
                fallback_mode=import_fallback,
                tool=tool,
                command=command,
                args=list(agent_arg) if agent_arg else None,
            )

            if "error" in res:
                click.echo(json.dumps(res), err=True)
                sys.exit(1)
            else:
                click.echo(json.dumps(res))
        except Exception as e:
            import logging

            logging.getLogger(__name__).debug("Error occurred", exc_info=True)
            click.echo(json.dumps({"source_id": None, "error": str(e)}), err=True)
            sys.exit(1)

    asyncio.run(run())


@cli.command(name="import-drive")
@click.argument("notebook_id")
@click.argument("url_or_id")
@click.option("--title", help="Title for the imported Drive document")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def import_drive(notebook_id, url_or_id, title, verbose):
    """Import a Google Drive document URL or ID as a source to a notebook."""
    setup_logging(verbose)

    async def run():
        try:
            # If it looks like a URL, pass it, otherwise assume it's just the URL itself, import_web_source handles URLs.
            # To be safe, construct a fake URL if it's just an ID
            if not url_or_id.startswith("http"):
                url = f"https://docs.google.com/document/d/{url_or_id}/edit"
            else:
                url = url_or_id

            res = await research.import_web_source(notebook_id, url, title=title)

            if "error" in res:
                click.echo(json.dumps(res), err=True)
                sys.exit(1)
            else:
                click.echo(json.dumps(res))
        except Exception as e:
            import logging

            logging.getLogger(__name__).debug("Error occurred", exc_info=True)
            click.echo(json.dumps({"source_id": None, "error": str(e)}), err=True)
            sys.exit(1)

    asyncio.run(run())


@cli.group(name="research")
def research_group():
    """Manage web research and source enrichment."""
    pass


@research_group.command(name="create")
@click.argument("notebook_id")
@click.argument("source_id")
@click.option(
    "--mode",
    type=click.Choice(["fast", "deep"]),
    default="fast",
    help="Research mode (default: fast)",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def research_create(notebook_id, source_id, mode, verbose):
    """Enrich a notebook with research based on a source guide and summary. Outputs task JSON."""
    setup_logging(verbose)

    async def run():
        try:
            task = await research.create_research_job(notebook_id, source_id, mode)
            click.echo(json.dumps(task))
            sys.stdout.flush()
        except Exception as e:
            import logging

            logging.getLogger(__name__).debug("Error occurred", exc_info=True)
            click.echo(json.dumps({"error": str(e)}), err=True)
            sys.exit(1)

    asyncio.run(run())


@research_group.command(name="poll")
@click.option("--max-imports", type=int, help="Maximum number of sources to import")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to poll.")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def research_poll(max_imports, arg_json, verbose):
    """Poll research tasks and import sources. Accepts input from --arg-json or stdin."""
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

        async for completed in research.poll_research_jobs(input_gen(), max_imports):
            click.echo(json.dumps(completed))
            sys.stdout.flush()

    asyncio.run(run())


@cli.command(name="dist-rsync")
@click.argument("notebook_id")
@click.argument("destination", required=False)
@click.option(
    "--method",
    type=click.Choice(["rsync", "rclone"]),
    help="Sync method (default: rsync)",
)
@click.option("--preset", help="Named rsync preset from config")
@click.option(
    "--podcast-dir",
    "-p",
    help="Base directory where podcasts are stored (default from config or out)",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def dist_rsync(notebook_id, destination, method, preset, podcast_dir, verbose):
    """Distribute a notebook's podcasts to a remote directory (rsync/rclone)."""
    setup_logging(verbose)

    async def run():
        try:
            from .utils import load_config

            config = load_config()

            final_dest = destination
            final_method = method or "rsync"

            if preset:
                ref_config = config.rsync.get(preset)
                if not ref_config:
                    click.echo(
                        json.dumps(
                            {"error": f"Rsync preset '{preset}' not found in config."}
                        ),
                        err=True,
                    )
                    sys.exit(1)
                final_dest = ref_config.destination
                final_method = method or ref_config.method

            if not final_dest:
                click.echo(
                    json.dumps(
                        {
                            "error": "Destination must be provided as an argument or via --preset."
                        }
                    ),
                    err=True,
                )
                sys.exit(1)

            res = await plex.sync_podcast(
                notebook_id, final_dest, final_method, podcast_dir, verbose
            )
            click.echo(json.dumps(res))
        except Exception as e:
            import logging

            logging.getLogger(__name__).debug("Error occurred", exc_info=True)
            click.echo(json.dumps({"error": str(e)}), err=True)
            sys.exit(1)

    asyncio.run(run())


@cli.command(name="dist-plex")
@click.argument("notebook_id")
@click.argument("plex_section_id", type=int, required=False)
@click.option("--preset", help="Named plex preset from config")
@click.option(
    "--podcast-dir",
    "-p",
    help="Base directory where podcasts are stored (default from config or out)",
)
@click.option(
    "--server-url",
    envvar="PLEX_SERVER_URL",
    help="Plex Server URL (e.g. http://localhost:32400)",
)
@click.option("--token", envvar="PLEX_TOKEN", help="Plex Authentication Token")
@click.option(
    "--server-library-path",
    envvar="PLEX_SERVER_LIBRARY_PATH",
    help="Remote library path on the Plex server",
)
@click.option(
    "--rsync-destination",
    help="Optional rsync/rclone destination to sync before triggering Plex (e.g. user@host:/path)",
)
@click.option(
    "--method",
    type=click.Choice(["rsync", "rclone"]),
    help="Sync method (default: rsync)",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def dist_plex(
    notebook_id,
    plex_section_id,
    preset,
    podcast_dir,
    server_url,
    token,
    server_library_path,
    rsync_destination,
    method,
    verbose,
):
    """Distribute a notebook's podcasts to a Plex library."""
    setup_logging(verbose)

    async def run():
        try:
            from .utils import load_config

            config = load_config()

            final_section_id = plex_section_id
            final_server_path = server_library_path
            final_rsync_dest = rsync_destination
            final_method = method or "rsync"

            if preset:
                ref_config = config.plex.get(preset)
                if not ref_config:
                    click.echo(
                        json.dumps(
                            {"error": f"Plex preset '{preset}' not found in config."}
                        ),
                        err=True,
                    )
                    sys.exit(1)
                final_section_id = plex_section_id or ref_config.section_id
                final_server_path = (
                    server_library_path or ref_config.server_library_path
                )
                if ref_config.rsync and ref_config.rsync.enabled:
                    if ref_config.rsync.ref:
                        rsync_ref = config.rsync.get(ref_config.rsync.ref)
                        if rsync_ref:
                            final_rsync_dest = (
                                rsync_destination or rsync_ref.destination
                            )
                            final_method = method or rsync_ref.method
                    else:
                        final_rsync_dest = (
                            rsync_destination or ref_config.rsync.destination
                        )
                        final_method = method or ref_config.rsync.method or "rsync"

            if final_section_id is None:
                click.echo(
                    json.dumps(
                        {
                            "error": "Plex section_id must be provided as an argument or via --preset."
                        }
                    ),
                    err=True,
                )
                sys.exit(1)

            res = await plex.sync_to_plex(
                notebook_id,
                final_section_id,
                podcast_dir,
                server_url,
                token,
                final_server_path,
                final_rsync_dest,
                final_method,
                verbose,
            )
            click.echo(json.dumps(res))
        except Exception as e:
            import logging

            logging.getLogger(__name__).debug("Error occurred", exc_info=True)
            click.echo(json.dumps({"error": str(e)}), err=True)
            sys.exit(1)

    asyncio.run(run())


@cli.command(name="init-podcast-notebook")
@click.option("--title", help="Title of the new notebook")
@click.option("--notebook-id", help="ID of an existing notebook to initialize locally")
@click.option(
    "--podcast-dir", help="Root podcast directory (default from config or podcasts)"
)
@click.option("--from-source", help="Source file or URL to upload as the first source")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def init_podcast_notebook(title, notebook_id, podcast_dir, from_source, verbose):
    """Create a new notebook or initialize an existing one locally."""
    setup_logging(verbose)

    async def run():
        if from_source:
            if notebook_id:
                click.echo(
                    json.dumps(
                        {
                            "error": "Cannot provide notebook-id when initializing from source."
                        }
                    ),
                    err=True,
                )
                sys.exit(1)
        else:
            if not title and not notebook_id:
                click.echo(
                    json.dumps(
                        {
                            "error": "Either --title or --notebook-id must be provided when not initializing from source."
                        }
                    ),
                    err=True,
                )
                sys.exit(1)

        try:
            res = await notebook.init_notebook(
                title=title,
                notebook_id=notebook_id,
                podcast_dir=podcast_dir,
                from_source=from_source,
            )
            click.echo(json.dumps(res))
        except Exception as e:
            import logging

            logging.getLogger(__name__).debug("Error occurred", exc_info=True)
            click.echo(json.dumps({"error": str(e)}), err=True)
            sys.exit(1)

    asyncio.run(run())


@click.group()
def workflow():
    """Higher-level podcast workflows."""
    pass


@workflow.command(name="run")
@click.argument("preset")
@click.argument("source_file", type=click.Path(exists=False), required=False)
@click.option(
    "--title",
    help="Title of the podcast / notebook. If not provided, it will be derived.",
)
@click.option(
    "--length",
    type=click.Choice(["short", "default", "long", "auto"]),
    help="Target length (default from config)",
)
@click.option(
    "--language",
    "-l",
    multiple=True,
    help="Target language (repeatable, default from config)",
)
@click.option(
    "--enrich-web/--no-enrich-web",
    default=None,
    help="Enrich notebook with web research (default: True)",
)
@click.option(
    "--generate-cover/--no-generate-cover",
    default=None,
    help="Generate AI album cover (default: True)",
)
@click.option(
    "--transcribe/--no-transcribe",
    default=None,
    help="Transcribe podcast (default: False)",
)
@click.option(
    "--podcast-dir", "-p", help="Base directory for storage (default from config)"
)
@click.option(
    "--resume",
    help="Resume a failed or interrupted workflow run with the specified notebook ID",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def run_workflow(
    preset,
    source_file,
    title,
    length,
    language,
    enrich_web,
    generate_cover,
    transcribe,
    podcast_dir,
    resume,
    verbose,
):
    """Run a named workflow preset from config."""
    setup_logging(verbose)

    async def run():
        is_resume = bool(resume)
        notebook_id = resume if is_resume else None

        if is_resume:
            if title or source_file:
                click.echo(
                    json.dumps(
                        {
                            "error": "Cannot provide title or source_file when resuming a workflow."
                        }
                    ),
                    err=True,
                )
                sys.exit(1)
        else:
            if not source_file:
                click.echo(
                    json.dumps(
                        {
                            "error": "Must provide source_file when starting a new workflow run."
                        }
                    ),
                    err=True,
                )
                sys.exit(1)

        from .utils import load_config

        config = load_config()

        wf_config = config.workflow.root.get(preset)
        if not wf_config:
            click.echo(
                json.dumps(
                    {"error": f"Workflow preset '{preset}' not found in config."}
                ),
                err=True,
            )
            sys.exit(1)

        from .workflows.deep_dive_article import workflow as dd_wf

        res = await dd_wf.run(
            preset_name=preset,
            title=title,
            source_file=source_file,
            notebook_id=notebook_id,
            length=length,
            languages=list(language) if language else None,
            enrich_web=enrich_web,
            generate_cover=generate_cover,
            transcribe=transcribe,
            podcast_dir=podcast_dir,
            resume=is_resume,
            verbose=verbose,
        )
        click.echo(json.dumps(res))

    asyncio.run(run())


cli.add_command(workflow)

if __name__ == "__main__":
    cli()
