"""Codified notification models and distribution metadata for workflows."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_serializer


@runtime_checkable
class WorkflowNotification(Protocol):
    """Protocol for codified workflow notification structures."""

    def to_notification_dict(self) -> dict[str, Any]:
        """Convert into human-readable notification key-values."""
        ...


class BaseWorkflowNotification(BaseModel):
    """Base model for codified workflow notifications."""

    model_config = ConfigDict(extra="forbid")

    workflow_type: str
    preset: str
    title: str
    notebook_url: str
    languages: list[str] = Field(default_factory=list)

    def to_notification_dict(self) -> dict[str, Any]:
        """Convert into human-readable notification key-values."""
        raise NotImplementedError


class DeepDiveArticleNotification(BaseWorkflowNotification):
    """Notification payload for deep-dive article workflows."""

    workflow_type: str = "deep_dive_article"
    source_url: str | None = None
    episodes: list[str] = Field(default_factory=list)

    def to_notification_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "workflow": "Deep Dive Article",
            "preset": self.preset,
            "title": self.title,
        }
        if self.source_url:
            data["source"] = self.source_url
        data.update(
            {
                "notebook": self.notebook_url,
                "languages": self.languages,
                "episodes": self.episodes,
            }
        )
        return data


class TopicWorkflowNotification(BaseWorkflowNotification):
    """Notification payload for topic-driven multi-podcast workflows."""

    workflow_type: str = "topic_workflow"
    topic: str
    podcasts: list[str] = Field(default_factory=list)

    def to_notification_dict(self) -> dict[str, Any]:
        return {
            "workflow": "Topic Podcast",
            "preset": self.preset,
            "title": self.title,
            "topic": self.topic,
            "notebook": self.notebook_url,
            "languages": self.languages,
            "podcasts": self.podcasts,
        }


class NotebookDistributionMetadata(BaseModel):
    """Notebook metadata used for artifact directory templating."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    url: str
    creation_date: str


class ArtifactDistributionMetadata(BaseModel):
    """Artifact metadata used for artifact file templating."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    language: str | None = None
    path: str | None = None
    lrc_path: str | None = None


NotificationPayload = (
    DeepDiveArticleNotification
    | TopicWorkflowNotification
    | BaseWorkflowNotification
    | dict[str, Any]
)


class WorkflowDistributionMetadata(BaseModel):
    """Complete codified metadata for a workflow distribution run."""

    model_config = ConfigDict(extra="forbid")

    id: str
    preset: str
    notebook: NotebookDistributionMetadata
    artifacts: list[ArtifactDistributionMetadata]
    notification: NotificationPayload

    @field_serializer("notification", when_used="always")
    def _serialize_notification(self, value: Any) -> dict[str, Any]:
        if hasattr(value, "to_notification_dict"):
            return value.to_notification_dict()
        if isinstance(value, BaseModel):
            return value.model_dump()
        return value
