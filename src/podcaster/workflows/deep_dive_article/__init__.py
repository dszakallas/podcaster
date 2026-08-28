"""Deep-dive article workflow plugin."""

import click
from pydantic import BaseModel

from podcaster.config import AppConfig
from podcaster.workflows import WorkflowPlugin

from .config import DeepDiveArticleConfig


def create_command(
    preset_name: str,
    app_config: AppConfig,
    workflow_config: BaseModel,
) -> click.Command:
    """Lazily load the command factory for a deep-dive workflow preset."""
    if not isinstance(workflow_config, DeepDiveArticleConfig):
        raise TypeError(
            f"Workflow preset '{preset_name}' must use DeepDiveArticleConfig."
        )

    from .cli import create_command as build_command

    return build_command(preset_name, app_config, workflow_config)


WORKFLOW_PLUGIN = WorkflowPlugin(
    type_name="deep_dive_article",
    config_type=DeepDiveArticleConfig,
    command_factory=create_command,
)
