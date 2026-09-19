"""Topic workflow plugin."""

import click
from pydantic import BaseModel

from podcaster.config import AppConfig
from podcaster.workflows import WorkflowPlugin

from ..notifications import TopicWorkflowNotification
from .config import TopicWorkflowConfig


def create_command(
    preset_name: str,
    app_config: AppConfig,
    workflow_config: BaseModel,
) -> click.Command:
    """Lazily load the command factory for a topic workflow preset."""
    if not isinstance(workflow_config, TopicWorkflowConfig):
        raise TypeError(
            f"Workflow preset '{preset_name}' must use TopicWorkflowConfig."
        )

    from .cli import create_command as build_command

    return build_command(preset_name, app_config, workflow_config)


WORKFLOW_PLUGIN = WorkflowPlugin(
    type_name="topic_workflow",
    config_type=TopicWorkflowConfig,
    command_factory=create_command,
    notification_type=TopicWorkflowNotification,
)
