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
@click.option("--album", help="Album name for metadata tagging")
@click.option("--date", help="Recording date for metadata tagging")
@click.option("--arg-json", multiple=True, help="JSON artifact object(s) to tag.")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def tag_podcast(cover, offset, album, date, arg_json, verbose):
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

        async for tagged in tagging.tag_artifacts(
            input_gen(), cover, offset, album=album, created_at=date
        ):
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


@cli.command(name="scrape")
@click.argument("target")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Do not execute the scraper, only log the command that would run",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def scrape(target, dry_run, verbose):
    """Scrape a target URL and output the result with metadata on a single NDJSON line."""
    setup_logging(verbose)

    async def run():
        res = await research.scrape(target, dry_run=dry_run)
        if res is not None:
            click.echo(json.dumps(res))
            sys.stdout.flush()

    try:
        asyncio.run(run())
    except Exception as e:
        import logging

        logging.getLogger(__name__).debug("Error occurred", exc_info=True)
        click.echo(json.dumps({"error": str(e)}), err=True)
        sys.exit(1)


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
@click.option(
    "--rclone-flag",
    multiple=True,
    help="Additional flags to pass to rclone (can be specified multiple times)",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def dist_rsync(
    notebook_id, destination, method, preset, podcast_dir, rclone_flag, verbose
):
    """Distribute a notebook's podcasts to a remote directory (rsync/rclone)."""
    setup_logging(verbose)

    async def run():
        try:
            from .utils import load_config

            config = load_config()

            final_dest = destination
            final_method = method or "rsync"
            final_rclone_flags = list(rclone_flag) if rclone_flag else None

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
                if not final_rclone_flags:
                    final_rclone_flags = ref_config.rclone_flags

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
                notebook_id,
                final_dest,
                final_method,
                podcast_dir,
                verbose,
                rclone_flags=final_rclone_flags,
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
@click.option(
    "--rclone-flag",
    multiple=True,
    help="Additional flags to pass to rclone (can be specified multiple times)",
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
    rclone_flag,
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
            final_rclone_flags = list(rclone_flag) if rclone_flag else None

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
                            if not final_rclone_flags:
                                final_rclone_flags = rsync_ref.rclone_flags
                    else:
                        final_rsync_dest = (
                            rsync_destination or ref_config.rsync.destination
                        )
                        final_method = method or ref_config.rsync.method or "rsync"
                        if not final_rclone_flags:
                            final_rclone_flags = ref_config.rsync.rclone_flags

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
                rclone_flags=final_rclone_flags,
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
@click.argument("source_file", type=click.Path(exists=False), required=True)
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
    verbose,
):
    """Run a named workflow preset from config."""
    setup_logging(verbose)

    async def run():
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
            notebook_id=None,
            length=length,
            languages=list(language) if language else None,
            enrich_web=enrich_web,
            generate_cover=generate_cover,
            transcribe=transcribe,
            podcast_dir=podcast_dir,
            resume=False,
            verbose=verbose,
        )
        click.echo(json.dumps(res))

    asyncio.run(run())


@workflow.command(name="resume")
@click.argument("notebook_id")
@click.option(
    "--podcast-dir", "-p", help="Base directory for storage (default from config)"
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def resume_workflow(notebook_id, podcast_dir, verbose):
    """Resume a failed or interrupted workflow run from the state file."""
    setup_logging(verbose)

    async def run():
        from pathlib import Path

        from .utils import find_notebook_dir, load_config

        config = load_config()
        local_dir = podcast_dir
        if not local_dir:
            local_dir = config.podcast_dir or "podcasts"

        # Find directory
        notebook_dir_name = find_notebook_dir(local_dir, notebook_id)
        if not notebook_dir_name:
            click.echo(
                json.dumps(
                    {
                        "error": f"Could not find directory for notebook ID: {notebook_id}"
                    }
                ),
                err=True,
            )
            sys.exit(1)

        notebook_dir_path = Path(local_dir) / notebook_dir_name

        # Load state to check the preset and make sure it exists
        from .workflows.deep_dive_article.state import WorkflowState

        state = WorkflowState.load(notebook_dir_path)
        if not state:
            click.echo(
                json.dumps(
                    {
                        "error": f"No state.json found for notebook ID {notebook_id} in {notebook_dir_path}"
                    }
                ),
                err=True,
            )
            sys.exit(1)

        preset = state.preset

        # Run the workflow with resume=True
        from .workflows.deep_dive_article import workflow as dd_wf

        res = await dd_wf.run(
            preset_name=preset,
            notebook_id=notebook_id,
            podcast_dir=local_dir,
            resume=True,
            verbose=verbose,
        )
        click.echo(json.dumps(res))

    try:
        asyncio.run(run())
    except Exception as e:
        import logging

        logging.getLogger(__name__).debug("Error occurred", exc_info=True)
        click.echo(json.dumps({"error": str(e)}), err=True)
        sys.exit(1)


@workflow.command(name="edit")
@click.argument("notebook_id")
@click.option(
    "--podcast-dir", "-p", help="Base directory for storage (default from config)"
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def workflow_edit(notebook_id, podcast_dir, verbose):
    """Open the workflow state file in EDITOR for editing."""
    setup_logging(verbose)

    import os
    from pathlib import Path

    from .utils import find_notebook_dir, load_config

    config = load_config()
    if not podcast_dir:
        podcast_dir = config.podcast_dir or "podcasts"

    notebook_dir_name = find_notebook_dir(podcast_dir, notebook_id)
    if not notebook_dir_name:
        click.echo(
            json.dumps(
                {"error": f"Could not find directory for notebook ID: {notebook_id}"}
            ),
            err=True,
        )
        sys.exit(1)

    state_file = Path(podcast_dir) / notebook_dir_name / "state.json"
    if not state_file.exists():
        click.echo(
            json.dumps(
                {
                    "error": f"No state.json found for notebook ID {notebook_id} in {state_file.parent}"
                }
            ),
            err=True,
        )
        sys.exit(1)

    editor = os.environ.get("EDITOR")
    try:
        if editor:
            click.edit(filename=str(state_file), editor=editor)
        else:
            click.edit(filename=str(state_file))
    except Exception as e:
        click.echo(
            json.dumps({"error": f"Failed to open editor: {str(e)}"}),
            err=True,
        )
        sys.exit(1)


cli.add_command(workflow)

if __name__ == "__main__":
    cli()
