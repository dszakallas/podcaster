"""Workflow orchestration CLI commands."""

import functools
import json
import logging
import sys
from collections.abc import Mapping
from typing import Any

import click

from podcaster.config import load_config
from podcaster.utils.cli import async_command, verbose_option
from podcaster.utils.dbos import (
    assert_workflow_version,
    ensure_dbos_initialized,
    shutdown_dbos,
    wait_for_workflow_result,
)

from . import cli


@click.group()
@verbose_option
def workflow():
    """Higher-level podcast workflows."""
    pass


cli.add_command(workflow)


class DynamicWorkflowRunGroup(click.Group):
    """Dynamic Click group for running workflow presets defined in config."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        try:
            config = load_config()
            return sorted(config.workflow.presets.root.keys())
        except Exception:
            return []

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        try:
            config = load_config()
        except Exception:
            return None

        if cmd_name not in config.workflow.presets.root:
            return None

        wf_item = config.workflow.presets.root[cmd_name]
        from podcaster.workflows import get_workflow_plugin

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

    from podcaster.workflows import load_workflow_definitions

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
