"""Tests for command-line JSON input validation."""

import asyncio
import logging
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import dbos
import pytest
from click.testing import CliRunner

from podcaster import cli as cli_module
from podcaster.cli import cli, parse_input_stream
from podcaster.models import PodcastGenTask
from podcaster.utils.dbos import (
    GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
    assert_workflow_version,
    shutdown_dbos,
    wait_for_workflow_result,
)


@pytest.mark.anyio
async def test_parse_input_stream_rejects_invalid_arg_json() -> None:
    with pytest.raises(ValueError, match="Invalid --arg-json payload"):
        async for _ in parse_input_stream(("not-json",), model_cls=PodcastGenTask):
            pass


@pytest.mark.anyio
async def test_stream_stdin_rejects_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module.sys, "stdin", io.StringIO("not-json\n"))

    with pytest.raises(ValueError, match="Invalid JSON on stdin line 1"):
        async for _ in cli_module.stream_stdin():
            pass


@pytest.mark.anyio
async def test_wait_for_workflow_result_cancels_the_workflow_on_interruption(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="podcaster.utils.dbos")
    waiting = asyncio.Event()

    async def wait_forever(_workflow_id: str) -> None:
        waiting.set()
        await asyncio.Event().wait()

    with (
        patch("dbos.DBOS.get_result_async", side_effect=wait_forever),
        patch("dbos.DBOS.cancel_workflow_async", new_callable=AsyncMock) as cancel,
    ):
        task = asyncio.create_task(wait_for_workflow_result("wf-1"))
        await waiting.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    cancel.assert_awaited_once_with("wf-1", cancel_children=True)
    assert "Graceful shutdown initiated; cancelling workflow wf-1" in caplog.text


def test_shutdown_dbos_waits_for_local_workflow_cleanup() -> None:
    with patch("dbos.DBOS.destroy") as destroy:
        shutdown_dbos()

    destroy.assert_called_once_with(
        workflow_completion_timeout_sec=GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS
    )


def test_workflow_resume_uses_dbos_async_api() -> None:
    dbos_config = object()
    with (
        patch(
            "podcaster.cli.load_config",
            return_value=SimpleNamespace(dbos=dbos_config),
        ),
        patch("podcaster.cli.ensure_dbos_initialized") as initialize_dbos,
        patch("podcaster.workflows.load_workflow_definitions") as load_definitions,
        patch(
            "dbos.DBOS.get_workflow_status_async",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(app_version="current"),
        ),
        patch.object(dbos.DBOS, "application_version", "current"),
        patch("dbos.DBOS.resume_workflow_async", new_callable=AsyncMock) as resume,
        patch(
            "podcaster.cli.wait_for_workflow_result",
            new_callable=AsyncMock,
            return_value={"status": "resumed"},
        ) as wait,
        patch("podcaster.cli.shutdown_dbos") as shutdown,
    ):
        result = CliRunner().invoke(cli, ["workflow", "resume", "wf-1"])

    assert result.exit_code == 0
    load_definitions.assert_called_once()
    initialize_dbos.assert_called_once_with(dbos_config)
    resume.assert_awaited_once_with("wf-1")
    wait.assert_awaited_once_with("wf-1")
    shutdown.assert_called_once()


def test_workflow_resume_rejects_different_dbos_application_versions() -> None:
    with pytest.raises(ValueError, match="cannot be resumed safely"):
        assert_workflow_version("wf-1", "previous", "current")


def test_workflow_resume_force_forks_incompatible_workflow() -> None:
    dbos_config = object()
    fork = SimpleNamespace(workflow_id="wf-fork")
    with (
        patch(
            "podcaster.cli.load_config",
            return_value=SimpleNamespace(dbos=dbos_config),
        ),
        patch("podcaster.cli.ensure_dbos_initialized"),
        patch("podcaster.workflows.load_workflow_definitions"),
        patch(
            "dbos.DBOS.get_workflow_status_async",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(app_version="previous"),
        ),
        patch.object(dbos.DBOS, "application_version", "current"),
        patch(
            "dbos.DBOS.list_workflow_steps_async",
            new_callable=AsyncMock,
            return_value=[{"function_id": 14}],
        ),
        patch(
            "dbos.DBOS.fork_workflow_async",
            new_callable=AsyncMock,
            return_value=fork,
        ) as fork_workflow,
        patch(
            "podcaster.cli.wait_for_workflow_result",
            new_callable=AsyncMock,
            return_value={"workflow_id": "wf-fork"},
        ) as wait,
        patch("podcaster.cli.shutdown_dbos"),
    ):
        result = CliRunner().invoke(cli, ["workflow", "resume", "--force", "wf-1"])

    assert result.exit_code == 0
    fork_workflow.assert_awaited_once_with(
        "wf-1", 15, application_version="current"
    )
    wait.assert_awaited_once_with("wf-fork")


def test_workflow_status_reports_dbos_step_details() -> None:
    workflow_status = SimpleNamespace(
        workflow_id="wf-1",
        name="deep_dive_article_workflow",
        status="SUCCESS",
        created_at="2026-08-27T10:00:00Z",
        updated_at="2026-08-27T10:05:00Z",
        error=None,
    )
    steps = [
        {
            "function_id": 1,
            "function_name": "podcaster.workflow.init_notebook_step",
            "output": None,
            "error": None,
            "child_workflow_id": None,
            "started_at_epoch_ms": 100,
            "completed_at_epoch_ms": 200,
        },
        {
            "function_id": 2,
            "function_name": "podcaster.workflow.generate_cover_step",
            "output": None,
            "error": RuntimeError("cover failed"),
            "child_workflow_id": None,
            "started_at_epoch_ms": 300,
            "completed_at_epoch_ms": 400,
        },
        {
            "function_id": 3,
            "function_name": "podcaster.workflow.distribute_step",
            "output": None,
            "error": None,
            "child_workflow_id": None,
            "started_at_epoch_ms": 500,
            "completed_at_epoch_ms": None,
        },
    ]

    with (
        patch(
            "podcaster.cli.load_config",
            return_value=SimpleNamespace(dbos=None),
        ),
        patch("podcaster.cli.ensure_dbos_initialized"),
        patch("dbos.DBOS.get_workflow_status", return_value=workflow_status),
        patch("dbos.DBOS.list_workflow_steps", return_value=steps),
    ):
        result = CliRunner().invoke(cli, ["workflow", "status", "wf-1"])

    assert result.exit_code == 0
    assert json.loads(result.output)["steps"] == [
        {
            "step_id": 1,
            "step_name": "podcaster.workflow.init_notebook_step",
            "status": "completed",
            "error": None,
            "started_at_epoch_ms": 100,
            "completed_at_epoch_ms": 200,
            "child_workflow_id": None,
        },
        {
            "step_id": 2,
            "step_name": "podcaster.workflow.generate_cover_step",
            "status": "failed",
            "error": "cover failed",
            "started_at_epoch_ms": 300,
            "completed_at_epoch_ms": 400,
            "child_workflow_id": None,
        },
        {
            "step_id": 3,
            "step_name": "podcaster.workflow.distribute_step",
            "status": "running",
            "error": None,
            "started_at_epoch_ms": 500,
            "completed_at_epoch_ms": None,
            "child_workflow_id": None,
        },
    ]


def test_workflow_list_uses_dbos_configuration() -> None:
    dbos_config = object()
    with (
        patch(
            "podcaster.cli.load_config",
            return_value=SimpleNamespace(dbos=dbos_config),
        ),
        patch("podcaster.cli.ensure_dbos_initialized") as initialize_dbos,
        patch("dbos.DBOS.list_workflows", return_value=[]),
    ):
        result = CliRunner().invoke(cli, ["workflow", "list"])

    assert result.exit_code == 0
    initialize_dbos.assert_called_once_with(dbos_config)
