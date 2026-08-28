"""Workflow plugin discovery."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pkgutil import iter_modules
from typing import TYPE_CHECKING, Callable

import click
from pydantic import BaseModel

if TYPE_CHECKING:
    from podcaster.config import AppConfig


@dataclass(frozen=True)
class WorkflowPlugin:
    """A workflow type and the CLI command factory that runs its presets."""

    type_name: str
    config_type: type[BaseModel]
    command_factory: Callable[[str, AppConfig, BaseModel], click.Command]


def discover_workflow_plugins() -> dict[str, WorkflowPlugin]:
    """Discover workflow plugins from direct subpackages of ``podcaster.workflows``."""
    plugins: dict[str, WorkflowPlugin] = {}
    for module in iter_modules(__path__, f"{__name__}."):
        if not module.ispkg or module.name.rsplit(".", maxsplit=1)[-1].startswith("_"):
            continue

        package = import_module(module.name)
        plugin = getattr(package, "WORKFLOW_PLUGIN", None)
        if not isinstance(plugin, WorkflowPlugin):
            raise TypeError(
                f"Workflow package '{module.name}' must declare a WORKFLOW_PLUGIN."
            )
        if plugin.type_name in plugins:
            raise ValueError(f"Duplicate workflow plugin type '{plugin.type_name}'.")
        plugins[plugin.type_name] = plugin
    return plugins


def get_workflow_plugin(type_name: str) -> WorkflowPlugin | None:
    """Return the discovered plugin for a workflow configuration type."""
    return discover_workflow_plugins().get(type_name)


def load_workflow_definitions() -> None:
    """Import workflow implementations so DBOS registers their durable functions."""
    for module in iter_modules(__path__, f"{__name__}."):
        if module.ispkg and not module.name.rsplit(".", maxsplit=1)[-1].startswith("_"):
            import_module(f"{module.name}.workflow")
