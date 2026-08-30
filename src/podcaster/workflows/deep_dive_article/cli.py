"""Click command factory for the deep-dive article workflow."""

import uuid

import click
import dbos

from podcaster.config import AppConfig
from podcaster.utils.cli import async_command, verbose_option
from podcaster.utils.dbos import shutdown_dbos, wait_for_workflow_result

from .config import DeepDiveArticleConfig
from .workflow import deep_dive_article_workflow


def create_command(
    preset_name: str,
    app_config: AppConfig,
    workflow_config: DeepDiveArticleConfig,
) -> click.Command:
    """Create the command that runs one deep-dive workflow preset."""

    @click.command(
        name=preset_name,
        help=f"Run '{preset_name}' (type: deep_dive_article) workflow.",
    )
    @click.argument("source_url", required=True)
    @click.option(
        "--workflow-id",
        "-w",
        help="Explicit workflow run ID (auto-generated UUID if omitted)",
    )
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
        help="Enrich notebook with web research (default from config)",
    )
    @click.option(
        "--generate-cover/--no-generate-cover",
        default=None,
        help="Generate AI album cover (default from config)",
    )
    @click.option(
        "--transcribe/--no-transcribe",
        default=None,
        help="Transcribe podcast (default from config)",
    )
    @click.option(
        "--workdir",
        "-W",
        help="Workflow output root (default from config or current directory)",
    )
    @verbose_option
    @async_command()
    async def run_cmd(
        source_url,
        workflow_id,
        title,
        length,
        language,
        enrich_web,
        generate_cover,
        transcribe,
        workdir,
    ):
        workdir = workdir or app_config.workflow.workdir

        wf_id = workflow_id or f"wf_{uuid.uuid4().hex[:12]}"
        lang_list = [lang.lower() for lang in language] if language else None

        try:
            handle = dbos.DBOS.start_workflow(
                deep_dive_article_workflow,
                preset_name=preset_name,
                wf_config=workflow_config,
                workdir=workdir,
                workflow_id=wf_id,
                title=title,
                source_url=source_url,
                notebook_id=None,
                length=length,
                languages=lang_list,
                enrich_web=enrich_web,
                generate_cover=generate_cover,
                transcribe=transcribe,
                gcp_config=app_config.gcp,
                notebooklm_config=app_config.notebooklm,
            )
            return await wait_for_workflow_result(handle.workflow_id)
        finally:
            shutdown_dbos()

    return run_cmd
