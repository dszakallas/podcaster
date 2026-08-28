"""Tests for workflow plugin discovery and CLI command mapping."""

from types import SimpleNamespace

import click
from click.testing import CliRunner

from podcaster.cli import cli
from podcaster.config import AppConfig, WorkflowConfig
from podcaster.workflows import discover_workflow_plugins
from podcaster.workflows.deep_dive_article.config import DeepDiveArticleConfig


def _deep_dive_workflow_config() -> dict:
    return {
        "type": "deep_dive_article",
        "podcast_generator": {},
        "importer": {},
        "enrich_web": {},
        "generate_cover": {},
        "transcribe": {"podcast_transcriber": {}},
        "tagging": {"spec": {}},
        "distribute": [],
    }


def _app_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "dbos": {},
            "scrapers": {},
            "agents": {},
            "podcast_generators": {},
            "podcast_transcribers": {},
            "importers": {},
            "podcast_tags": {},
            "workflow": {"presets": {}},
            "notifiers": {},
            "distributions": {},
            "gcp": {},
        }
    )


def test_workflow_plugin_declares_its_config_type_and_command_factory():
    plugin = discover_workflow_plugins()["deep_dive_article"]
    workflow_config = WorkflowConfig.model_validate(
        {"presets": {"daily": _deep_dive_workflow_config()}}
    ).presets.root["daily"]

    assert plugin.config_type is DeepDiveArticleConfig
    assert plugin.command_factory("daily", _app_config(), workflow_config).name == "daily"


def test_workflow_config_uses_the_plugin_declared_configuration_type():
    config = WorkflowConfig.model_validate(
        {"presets": {"daily": _deep_dive_workflow_config()}}
    )

    assert isinstance(config.presets.root["daily"], DeepDiveArticleConfig)


def test_workflow_framework_initializes_dbos_and_passes_loaded_configs(monkeypatch):
    workflow_config = WorkflowConfig.model_validate(
        {"presets": {"daily": _deep_dive_workflow_config()}}
    ).presets.root["daily"]
    dbos_config = object()
    config = SimpleNamespace(
        workflow=SimpleNamespace(presets=SimpleNamespace(root={"daily": workflow_config})),
        dbos=dbos_config,
    )
    received = []

    def create_command(preset_name, app_config, loaded_workflow_config):
        received.append((preset_name, app_config, loaded_workflow_config))
        return click.Command(
            preset_name,
            callback=lambda: received.append("plugin command ran"),
            help="Plugin command",
        )

    plugin = SimpleNamespace(command_factory=create_command)
    monkeypatch.setattr("podcaster.cli.load_config", lambda: config)
    monkeypatch.setattr("podcaster.cli.ensure_dbos_initialized", lambda value: received.append(value))
    monkeypatch.setattr("podcaster.workflows.get_workflow_plugin", lambda _: plugin)

    result = CliRunner().invoke(cli, ["workflow", "run", "daily"])

    assert result.exit_code == 0
    assert received == [
        ("daily", config, workflow_config),
        dbos_config,
        "plugin command ran",
    ]


def test_workflow_framework_does_not_initialize_dbos_for_command_help(monkeypatch):
    workflow_config = WorkflowConfig.model_validate(
        {"presets": {"daily": _deep_dive_workflow_config()}}
    ).presets.root["daily"]
    config = SimpleNamespace(
        workflow=SimpleNamespace(presets=SimpleNamespace(root={"daily": workflow_config})),
        dbos=object(),
    )
    initialized = []
    plugin = SimpleNamespace(
        command_factory=lambda name, app_config, preset_config: click.Command(
            name, callback=lambda: None
        )
    )
    monkeypatch.setattr("podcaster.cli.load_config", lambda: config)
    monkeypatch.setattr(
        "podcaster.cli.ensure_dbos_initialized", lambda value: initialized.append(value)
    )
    monkeypatch.setattr("podcaster.workflows.get_workflow_plugin", lambda _: plugin)

    result = CliRunner().invoke(cli, ["workflow", "run", "daily", "--help"])

    assert result.exit_code == 0
    assert initialized == []
