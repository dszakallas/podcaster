import asyncio
import functools
import json
import logging
import os
import re
import sys
from typing import Any, AsyncIterator, Optional, Type, TypeVar

import click
from pydantic import BaseModel

from . import cover, notebook, research, tagging, transcription
from .audio_gen import core as audio_gen_core
from .models import (
    CoverTask,
    PodcastGenArtifact,
    PodcastGenTask,
    ResearchTask,
    TranscriptionTask,
)
from .utils import load_config, resolve_notebook_dir_path, setup_logging

TModel = TypeVar("TModel", bound=BaseModel)


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


@click.group()
@verbose_option
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


async def parse_input_stream(
    arg_json: Optional[tuple[str, ...] | list[str]] = None,
    model_cls: Optional[Type[TModel]] = None,
) -> AsyncIterator[Any]:
    """Helper to yield parsed and validated objects/models from --arg-json options or stdin."""
    if arg_json:
        for aj in arg_json:
            try:
                if model_cls:
                    yield model_cls.model_validate_json(aj)
                else:
                    yield json.loads(aj)
            except Exception:
                continue
    else:
        async for item in stream_stdin():
            if model_cls:
                try:
                    yield model_cls.model_validate(item)
                except Exception:
                    continue
            else:
                yield item


def async_command(stream: bool = False):
    """Decorator to standardise asyncio execution, logging, JSON output, and error handling for Click commands."""

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
                            elif isinstance(item, dict):
                                click.echo(json.dumps(item))
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
                except Exception as e:
                    logging.getLogger(__name__).debug("Error occurred", exc_info=True)
                    click.echo(json.dumps({"error": str(e)}), err=True)
                    sys.exit(1)

            asyncio.run(runner())

        return wrapper

    return decorator


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
    return audio_gen_core.generate_tasks(
        notebook_id,
        type,
        list(language) if language else [],
        length,
        format_args_json,
        dry_run,
        generator_key=generator_key,
    )


@podcast_group.command(name="poll")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to poll.")
@verbose_option
@async_command(stream=True)
async def podcast_poll(arg_json):
    """Poll audio generation tasks. Accepts input from --arg-json or stdin."""
    return audio_gen_core.poll_tasks(
        parse_input_stream(arg_json, model_cls=PodcastGenTask)
    )


@podcast_group.command(name="download")
@click.option(
    "--podcast-dir", "-p", help="Output directory (default from config or podcasts)"
)
@click.option("--arg-json", multiple=True, help="JSON artifact object(s) to download.")
@verbose_option
@async_command(stream=True)
async def podcast_download(podcast_dir, arg_json):
    """Download podcast artifacts. Accepts input from --arg-json or stdin."""
    return audio_gen_core.download_artifacts(
        parse_input_stream(arg_json, model_cls=PodcastGenTask), podcast_dir
    )


@cli.group(name="cover")
@verbose_option
def cover_group():
    """Manage podcast cover generation."""
    pass


@cover_group.command(name="create")
@click.argument("notebook_id")
@verbose_option
@async_command()
async def cover_create(notebook_id):
    """Submit cover generation task. Outputs task JSON."""
    return await cover.create_cover_job(notebook_id)


@cover_group.command(name="poll")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to poll.")
@verbose_option
@async_command(stream=True)
async def cover_poll(arg_json):
    """Poll cover generation tasks. Accepts input from --arg-json or stdin."""
    return cover.poll_cover_jobs(parse_input_stream(arg_json, model_cls=CoverTask))


@cover_group.command(name="download")
@click.option(
    "--podcast-dir", "-p", help="Output directory (default from config or podcasts)"
)
@click.option("--arg-json", multiple=True, help="JSON artifact object(s) to download.")
@verbose_option
@async_command(stream=True)
async def cover_download(podcast_dir, arg_json):
    """Download cover generation results. Accepts input from --arg-json or stdin."""
    return cover.download_cover_jobs(
        parse_input_stream(arg_json, model_cls=CoverTask), podcast_dir
    )


@cli.group(name="transcription")
@verbose_option
def transcription_group():
    """Manage speech transcription."""
    pass


@transcription_group.command(name="create")
@click.option(
    "--arg-json", multiple=True, help="JSON artifact object(s) to transcribe."
)
@click.option(
    "--transcriber-key",
    default="default",
    help="Podcast transcriber key from configuration (default: default)",
)
@verbose_option
@async_command(stream=True)
async def transcription_create(arg_json, transcriber_key):
    """Start transcription tasks for podcast artifacts. Accepts input from --arg-json or stdin."""
    return transcription.create_transcription_jobs(
        parse_input_stream(arg_json, model_cls=PodcastGenArtifact),
        transcriber_key=transcriber_key,
    )


@transcription_group.command(name="poll")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to poll.")
@verbose_option
@async_command(stream=True)
async def transcription_poll(arg_json):
    """Poll speech recognition batch jobs. Accepts input from --arg-json or stdin."""
    return transcription.poll_transcription_jobs(
        parse_input_stream(arg_json, model_cls=TranscriptionTask)
    )


@transcription_group.command(name="download")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to download.")
@verbose_option
@async_command(stream=True)
async def transcription_download(arg_json):
    """Download transcription result and write JSON and LRC files. Accepts input from --arg-json or stdin."""
    return transcription.download_transcription_jobs(
        parse_input_stream(arg_json, model_cls=TranscriptionTask)
    )


@cli.command(name="tag-podcast")
@click.option("--cover", help="Path to cover image")
@click.option("--offset", default=0, help="Starting track number offset (default: 0)")
@click.option("--album", help="Album name for metadata tagging")
@click.option("--date", help="Recording date for metadata tagging")
@click.option("--arg-json", multiple=True, help="JSON artifact object(s) to tag.")
@verbose_option
@async_command(stream=True)
async def tag_podcast(cover, offset, album, date, arg_json):
    """Tag podcast artifacts with metadata. Accepts input from --arg-json or stdin."""
    return tagging.tag_artifacts(
        parse_input_stream(arg_json, model_cls=PodcastGenArtifact),
        cover,
        offset,
        album=album,
        created_at=date,
    )


@cli.command(name="list-podcasts")
@click.option(
    "--podcast-dir", "-p", help="Base directory for storage (default from config)"
)
@verbose_option
def list_podcasts(podcast_dir):
    """List locally available podcasts and their notebook IDs."""
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
        logging.getLogger(__name__).debug("Error occurred", exc_info=True)
        click.echo(json.dumps({"error": str(e)}), err=True)
        sys.exit(1)


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
    return await research.import_source(
        notebook_id, url, importer=importer, title=title
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

    return await research.import_source(
        notebook_id, url, importer=importer, title=title
    )


@cli.command(name="scrape")
@click.argument("target")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Do not execute the scraper, only log the command that would run",
)
@verbose_option
@async_command()
async def scrape(target, dry_run):
    """Scrape a target URL and output the result with metadata on a single NDJSON line."""
    return await research.scrape(target, dry_run=dry_run)


@cli.group(name="research")
@verbose_option
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
@verbose_option
@async_command()
async def research_create(notebook_id, source_id, mode):
    """Enrich a notebook with research based on a source guide and summary. Outputs task JSON."""
    return await research.create_research_job(notebook_id, source_id, mode)


@research_group.command(name="poll")
@click.option("--max-imports", type=int, help="Maximum number of sources to import")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to poll.")
@click.option(
    "--fallback-mechanism",
    default="ignore",
    help="Importer to use on failure or 'ignore' (default: ignore)",
)
@verbose_option
@async_command(stream=True)
async def research_poll(max_imports, arg_json, fallback_mechanism):
    """Poll research tasks and import sources. Accepts input from --arg-json or stdin."""
    return research.poll_research_jobs(
        parse_input_stream(arg_json, model_cls=ResearchTask),
        max_imports,
        fallback_mechanism=fallback_mechanism,
    )


@cli.command(name="distribute")
@click.argument("notebook_id")
@click.option(
    "--preset",
    default="default",
    show_default=True,
    help="Named distribution preset from config",
)
@click.option(
    "--podcast-dir",
    "-p",
    help="Base directory where podcasts are stored (default from config or out)",
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
    notebook_id,
    preset,
    podcast_dir,
    flag,
):
    """Distribute a notebook's podcasts using a named distribution preset."""
    from .distribution import build_distribution

    config = load_config()

    if preset not in config.distributions:
        raise ValueError(f"Distribution preset '{preset}' not found in configuration.")

    dist_obj = build_distribution(config.distributions[preset], name=preset)
    dist_any: Any = dist_obj
    if (
        flag
        and hasattr(dist_any, "flags")
        and isinstance(getattr(dist_any, "flags", None), list)
    ):
        dist_any.flags.extend(flag)

    return await dist_obj.distribute(notebook_id, podcast_dir=podcast_dir)


@cli.command(name="init-podcast-notebook")
@click.option("--title", help="Title of the new notebook")
@click.option("--notebook-id", help="ID of an existing notebook to initialize locally")
@click.option(
    "--podcast-dir", help="Root podcast directory (default from config or podcasts)"
)
@click.option("--from-source", help="Source file or URL to upload as the first source")
@verbose_option
@async_command()
async def init_podcast_notebook(title, notebook_id, podcast_dir, from_source):
    """Create a new notebook or initialize an existing one locally."""
    if from_source and notebook_id:
        raise ValueError("Cannot provide notebook-id when initializing from source.")
    if not from_source and not title and not notebook_id:
        raise ValueError(
            "Either --title or --notebook-id must be provided when not initializing from source."
        )

    return await notebook.init_notebook(
        title=title,
        notebook_id=notebook_id,
        podcast_dir=podcast_dir,
        from_source=from_source,
    )


@click.group()
@verbose_option
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
@verbose_option
@async_command()
async def run_workflow(
    preset,
    source_file,
    title,
    length,
    language,
    enrich_web,
    generate_cover,
    transcribe,
    podcast_dir,
):
    """Run a named workflow preset from config."""
    if not source_file:
        raise ValueError("Must provide source_file when starting a new workflow run.")

    config = load_config()

    wf_config = config.workflow.root.get(preset)
    if not wf_config:
        raise ValueError(f"Workflow preset '{preset}' not found in config.")

    from .workflows.deep_dive_article import workflow as dd_wf

    return await dd_wf.run(
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
    )


@workflow.command(name="resume")
@click.argument("notebook_id")
@click.option(
    "--podcast-dir", "-p", help="Base directory for storage (default from config)"
)
@verbose_option
@async_command()
async def resume_workflow(notebook_id, podcast_dir):
    """Resume a failed or interrupted workflow run from the state file."""
    from .workflows.deep_dive_article import workflow as dd_wf
    from .workflows.deep_dive_article.state import WorkflowState

    notebook_dir_path = resolve_notebook_dir_path(notebook_id, podcast_dir)
    state = WorkflowState.load(notebook_dir_path)
    if not state:
        raise ValueError(
            f"No state.json found for notebook ID {notebook_id} in {notebook_dir_path}"
        )

    return await dd_wf.run(
        preset_name=state.preset,
        notebook_id=notebook_id,
        podcast_dir=str(notebook_dir_path.parent),
        resume=True,
    )


@workflow.command(name="edit")
@click.argument("notebook_id")
@click.option(
    "--podcast-dir", "-p", help="Base directory for storage (default from config)"
)
@verbose_option
def workflow_edit(notebook_id, podcast_dir):
    """Open the workflow state file in EDITOR for editing."""
    try:
        notebook_dir_path = resolve_notebook_dir_path(notebook_id, podcast_dir)
        state_file = notebook_dir_path / "state.json"
        if not state_file.exists():
            raise ValueError(
                f"No state.json found for notebook ID {notebook_id} in {state_file.parent}"
            )

        editor = os.environ.get("EDITOR")
        click.edit(filename=str(state_file), editor=editor)
    except Exception as e:
        logging.getLogger(__name__).debug("Error occurred", exc_info=True)
        click.echo(json.dumps({"error": str(e)}), err=True)
        sys.exit(1)


cli.add_command(workflow)

if __name__ == "__main__":
    cli()
