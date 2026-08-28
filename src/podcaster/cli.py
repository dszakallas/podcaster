import asyncio
import functools
import json
import logging
import os
import re
import sys
from typing import Any, AsyncIterator, Mapping, Optional, Type, TypeVar

import click
from pydantic import BaseModel, ValidationError

from . import cover, notebook, research, tagging, transcription
from .audio_gen import core as audio_gen_core
from .config import DEFAULT_COVER_MODEL, load_config
from .models import (
    CoverTask,
    PodcastGenArtifact,
    PodcastGenTask,
    ResearchTask,
    TranscriptionTask,
)
from .utils.cli import async_command, verbose_option
from .utils.dbos import (
    assert_workflow_version,
    ensure_dbos_initialized,
    shutdown_dbos,
    wait_for_workflow_result,
)
from .utils.notebooklm import get_notebooklm_client

TModel = TypeVar("TModel", bound=BaseModel)


@click.group()
@verbose_option
def cli():
    """Podcaster automation tools."""
    pass


async def stream_stdin() -> AsyncIterator[Any]:
    """Helper to stream JSON objects from stdin."""
    loop = asyncio.get_event_loop()
    line_number = 0
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        line_number += 1
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON on stdin line {line_number}") from e


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
            except (json.JSONDecodeError, ValidationError) as e:
                raise ValueError("Invalid --arg-json payload") from e
    else:
        async for item in stream_stdin():
            if model_cls:
                try:
                    yield model_cls.model_validate(item)
                except ValidationError as e:
                    raise ValueError("Invalid JSON input from stdin") from e
            else:
                yield item


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
    config = load_config()
    gen_cfg = config.podcast_generators.get(generator_key)
    if not gen_cfg:
        raise ValueError(
            f"Generator preset '{generator_key}' not found in configuration"
        )
    format_args: dict[str, Any] = (
        json.loads(format_args_json) if format_args_json else {}
    )
    return audio_gen_core.create_podcast_audio_jobs(
        notebook_id,
        type,
        [lang.lower() for lang in language] if language else [],
        length,
        format_args,
        generator_config=gen_cfg,
        notebooklm_config=config.notebooklm,
        dry_run=dry_run,
    )


@podcast_group.command(name="poll")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to poll.")
@verbose_option
@async_command(stream=True)
async def podcast_poll(arg_json):
    """Poll audio generation tasks. Accepts input from --arg-json or stdin."""
    config = load_config()
    return audio_gen_core.poll_tasks(
        parse_input_stream(arg_json, model_cls=PodcastGenTask), config.notebooklm
    )


@podcast_group.command(name="download")
@click.option(
    "--workdir",
    "-W",
    help="Output working directory (default: current directory)",
)
@click.option("--arg-json", multiple=True, help="JSON artifact object(s) to download.")
@verbose_option
@async_command(stream=True)
async def podcast_download(workdir, arg_json):
    """Download podcast artifacts. Accepts input from --arg-json or stdin."""
    config = load_config()
    working_dir = workdir or "."
    return audio_gen_core.download_artifacts(
        parse_input_stream(arg_json, model_cls=PodcastGenTask),
        working_dir,
        config.notebooklm,
    )


@cli.group(name="cover")
@verbose_option
def cover_group():
    """Manage podcast cover generation."""
    pass


@cover_group.command(name="create")
@click.argument("notebook_id")
@click.option(
    "--model",
    default=DEFAULT_COVER_MODEL,
    help="Model to use for cover generation.",
)
@verbose_option
@async_command()
async def cover_create(notebook_id, model):
    """Submit cover generation task. Outputs task JSON."""
    config = load_config()
    async with get_notebooklm_client(config.notebooklm) as client:
        return await cover.create_cover_job(notebook_id, client, model=model)


@cover_group.command(name="poll")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to poll.")
@verbose_option
@async_command(stream=True)
async def cover_poll(arg_json):
    """Poll cover generation tasks. Accepts input from --arg-json or stdin."""
    return cover.poll_cover_jobs(parse_input_stream(arg_json, model_cls=CoverTask))


@cover_group.command(name="download")
@click.option(
    "--workdir",
    "-W",
    help="Output working directory (default: current directory)",
)
@click.option("--arg-json", multiple=True, help="JSON artifact object(s) to download.")
@verbose_option
@async_command(stream=True)
async def cover_download(workdir, arg_json):
    """Download cover generation results. Accepts input from --arg-json or stdin."""
    working_dir = workdir or "."
    return cover.download_cover_jobs(
        parse_input_stream(arg_json, model_cls=CoverTask), working_dir
    )


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


@cli.command(name="tag-podcast")
@click.option("--cover", help="Path to cover image")
@click.option("--offset", default=0, help="Starting track number offset (default: 0)")
@click.option("--album", help="Album name for metadata tagging")
@click.option("--date", help="Recording date for metadata tagging")
@click.option("--arg-json", multiple=True, help="JSON artifact object(s) to tag.")
@click.option("--preset", required=True, help="Podcast tag preset to apply")
@verbose_option
@async_command(stream=True)
async def tag_podcast(cover, offset, album, date, arg_json, preset):
    """Tag podcast artifacts with metadata. Accepts input from --arg-json or stdin."""
    config = load_config()
    tags_config = config.podcast_tags.get(preset)
    if not tags_config:
        raise ValueError(f"Podcast tag preset '{preset}' not found in configuration.")
    return tagging.tag_artifacts(
        parse_input_stream(arg_json, model_cls=PodcastGenArtifact),
        cover,
        offset,
        album=album,
        created_at=date,
        tags_config=tags_config,
    )


@cli.command(name="list-podcasts")
@click.option(
    "--workdir",
    "-W",
    default=".",
    help="Directory to search (default: current directory)",
)
@verbose_option
def list_podcasts(workdir):
    """List locally available podcasts and their notebook IDs."""
    if not os.path.exists(workdir):
        click.echo(json.dumps({"error": f"Directory not found: {workdir}"}), err=True)
        sys.exit(1)

    try:
        for item in os.listdir(workdir):
            item_path = os.path.join(workdir, item)
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
@verbose_option
@async_command()
async def scrape(target, scraper_name, dry_run):
    """Scrape a target URL and output the result with metadata on a single NDJSON line."""
    config = load_config()
    scraper_cfg = config.scrapers.get(scraper_name)
    if not scraper_cfg:
        raise ValueError(f"Scraper '{scraper_name}' not found in configuration.")
    return await research.scrape_source(
        target, dry_run=dry_run, scraper_config=scraper_cfg
    )


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
    config = load_config()
    async with get_notebooklm_client(config.notebooklm) as client:
        return await research.create_research_job(
            notebook_id, source_id, client=client, mode=mode
        )


@research_group.command(name="poll")
@click.option("--arg-json", multiple=True, help="JSON task object(s) to poll.")
@click.option(
    "--fallback-importer",
    help="Importer preset name to use on failure",
)
@click.option(
    "--max-import-failures",
    type=int,
    help="Maximum allowed import failures before failing the research job",
)
@verbose_option
@async_command(stream=True)
async def research_poll(arg_json, fallback_importer, max_import_failures):
    """Poll research tasks and import sources. Accepts input from --arg-json or stdin."""
    config = load_config()
    fallback_cfg = None
    if fallback_importer:
        if fallback_importer in config.importers:
            fallback_cfg = config.importers[fallback_importer]
        else:
            raise ValueError(
                f"Importer '{fallback_importer}' not found in configuration."
            )
    return research.poll_research_jobs(
        parse_input_stream(arg_json, model_cls=ResearchTask),
        config.notebooklm,
        fallback_importer=fallback_cfg,
        max_import_failures=max_import_failures,
    )


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
    from .distribution import build_distribution

    config = load_config()
    working_dir = workdir or "."

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

    return await dist_obj.distribute(working_dir=working_dir)


@cli.command(name="init-podcast-notebook")
@click.option("--title", help="Title of the new notebook")
@click.option("--notebook-id", help="ID of an existing notebook to fetch")
@click.option("--from-source", help="Source file or URL to upload as the first source")
@click.option(
    "--importer",
    default="default",
    help="Importer to use for --from-source",
)
@verbose_option
@async_command()
async def init_podcast_notebook(title, notebook_id, from_source, importer):
    """Create a new notebook or fetch an existing one."""
    config = load_config()
    importer_cfg = config.importers.get(importer)
    if not importer_cfg:
        raise ValueError(f"Importer '{importer}' not found in configuration.")
    if from_source and notebook_id:
        raise ValueError("Cannot provide notebook-id when initializing from source.")
    if not from_source and not title and not notebook_id:
        raise ValueError(
            "Either --title or --notebook-id must be provided when not initializing from source."
        )

    async with get_notebooklm_client(config.notebooklm) as client:
        return await notebook.init_notebook(
            importer=importer_cfg,
            client=client,
            title=title,
            notebook_id=notebook_id,
            from_source=from_source,
        )


@click.group()
@verbose_option
def workflow():
    """Higher-level podcast workflows."""
    pass


class DynamicWorkflowRunGroup(click.Group):
    """Dynamic Click group for running workflow presets defined in config."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        try:
            config = load_config()
            return sorted(config.workflow.presets.root.keys())
        except Exception:
            return []

    def get_command(self, ctx: click.Context, cmd_name: str) -> Optional[click.Command]:
        try:
            config = load_config()
        except Exception:
            return None

        if cmd_name not in config.workflow.presets.root:
            return None

        wf_item = config.workflow.presets.root[cmd_name]
        from .workflows import get_workflow_plugin

        workflow_type = wf_item.model_dump().get("type")
        if not isinstance(workflow_type, str):
            raise ValueError(
                f"Workflow preset '{cmd_name}' does not declare a string 'type'."
            )

        plugin = get_workflow_plugin(workflow_type)
        if plugin is None:
            raise ValueError(
                f"Unknown workflow type '{workflow_type}' for preset '{cmd_name}'."
            )

        command = plugin.command_factory(cmd_name, config, wf_item)
        if command.callback is None:
            raise ValueError(
                f"Workflow plugin '{workflow_type}' must provide a command callback."
            )

        callback = command.callback

        @functools.wraps(callback)
        def initialize_dbos_then_run(*args, **kwargs):
            ensure_dbos_initialized(config.dbos)
            return callback(*args, **kwargs)

        command.callback = initialize_dbos_then_run
        return command


@workflow.group(name="run", cls=DynamicWorkflowRunGroup)
def workflow_run():
    """Run a named workflow preset from config."""
    pass


@workflow.command(name="resume")
@click.argument("workflow_id")
@click.option(
    "--force",
    is_flag=True,
    help="Fork an incompatible workflow under the current DBOS application version.",
)
@verbose_option
@async_command()
async def resume_workflow(workflow_id, force):
    """Resume a failed or interrupted workflow run in DBOS."""
    import dbos

    from .workflows import load_workflow_definitions

    config = load_config()
    load_workflow_definitions()
    ensure_dbos_initialized(config.dbos)

    try:
        workflow_status = await dbos.DBOS.get_workflow_status_async(workflow_id)
        if workflow_status is None:
            raise ValueError(f"Workflow '{workflow_id}' was not found.")

        logger = logging.getLogger(__name__)
        current_version = dbos.DBOS.application_version
        if workflow_status.app_version != current_version:
            if not force:
                assert_workflow_version(
                    workflow_id,
                    workflow_status.app_version,
                    current_version,
                )

            steps = await dbos.DBOS.list_workflow_steps_async(
                workflow_id, load_output=False
            )
            start_step = max((step["function_id"] for step in steps), default=0) + 1
            handle = await dbos.DBOS.fork_workflow_async(
                workflow_id,
                start_step,
                application_version=current_version,
            )
            logger.warning(
                "Forked incompatible workflow %s as %s from step %s",
                workflow_id,
                handle.workflow_id,
                start_step,
            )
            return await wait_for_workflow_result(handle.workflow_id)

        logger.info("Resuming DBOS workflow: %s", workflow_id)
        await dbos.DBOS.resume_workflow_async(workflow_id)
        return await wait_for_workflow_result(workflow_id)
    finally:
        shutdown_dbos()


@workflow.command(name="status")
@click.argument("workflow_id")
@verbose_option
def status_workflow(workflow_id):
    """Get status and step breakdown for a DBOS workflow run."""
    import dbos

    config = load_config()
    ensure_dbos_initialized(config.dbos)

    wf_status = dbos.DBOS.get_workflow_status(workflow_id)
    if not wf_status:
        click.echo(f"Workflow run '{workflow_id}' not found.", err=True)
        sys.exit(1)

    steps = dbos.DBOS.list_workflow_steps(workflow_id)

    def format_step(step: Mapping[str, Any]) -> dict[str, Any]:
        error = step.get("error")
        completed_at = step.get("completed_at_epoch_ms")
        started_at = step.get("started_at_epoch_ms")

        if error is not None:
            status = "failed"
        elif completed_at is not None:
            status = "completed"
        elif started_at is not None:
            status = "running"
        else:
            status = "pending"

        return {
            "step_id": step.get("function_id"),
            "step_name": step.get("function_name", "unknown"),
            "status": status,
            "error": str(error) if error is not None else None,
            "started_at_epoch_ms": started_at,
            "completed_at_epoch_ms": completed_at,
            "child_workflow_id": step.get("child_workflow_id"),
        }

    res = {
        "workflow_id": wf_status.workflow_id,
        "name": wf_status.name,
        "status": str(wf_status.status),
        "created_at": str(wf_status.created_at),
        "updated_at": str(wf_status.updated_at),
        "steps": [format_step(step) for step in (steps or [])],
    }
    if wf_status.error:
        res["error"] = str(wf_status.error)

    click.echo(json.dumps(res, indent=2))


@workflow.command(name="list")
@verbose_option
def list_workflows():
    """List recent DBOS workflow executions."""
    import dbos

    config = load_config()
    ensure_dbos_initialized(config.dbos)

    workflows = dbos.DBOS.list_workflows()
    runs = []
    for wf in workflows:
        runs.append(
            {
                "workflow_id": wf.workflow_id,
                "name": wf.name,
                "status": str(wf.status),
                "created_at": str(wf.created_at),
            }
        )

    click.echo(json.dumps(runs, indent=2))


cli.add_command(workflow)

if __name__ == "__main__":
    cli()
