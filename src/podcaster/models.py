from __future__ import annotations

from enum import StrEnum
from typing import Any, NotRequired, TypedDict

from pydantic import BaseModel, Field


def _empty_metadata() -> PodcastArtifactMetadata:
    return {}


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class BaseTask(BaseModel):
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    error: str | None = None
    type: str = ""


class PodcastGenTask(BaseTask):
    type: str = "podcast_gen"
    notebook_id: str
    title: str | None = None
    eta: float = 10.0
    generation_started_at: float | None = None
    created_at: str | None = None
    metadata: PodcastArtifactMetadata = Field(default_factory=_empty_metadata)


class PodcastGenArtifact(BaseModel):
    notebook_id: str
    artifact_id: str
    title: str
    path: str
    filename: str
    lrc_path: str | None = None
    transcript_path: str | None = None
    metadata: PodcastArtifactMetadata = Field(default_factory=_empty_metadata)


class ResearchTask(BaseTask):
    type: str = "research"
    notebook_id: str
    source_id: str
    topic: str
    summary: str
    suggested_duration: str


class ResearchResult(ResearchTask):
    found_count: int = 0
    imported_count: int = 0
    imported: list[dict[str, Any]] = Field(default_factory=list)


class CoverTask(BaseTask):
    type: str = "cover"
    notebook_id: str
    image_gen_prompt: str
    cover_path: str | None = None


class TranscriptionTask(BaseTask):
    type: str = "transcription"
    artifact_id: str
    path: str
    gcs_uri: str | None = None
    preprocessed_path: str | None = None
    bcp47_lang: str = "en-US"
    speed_factor: float = 1.0
    lrc_path: str | None = None
    transcript_path: str | None = None
    metadata: PodcastArtifactMetadata = Field(default_factory=_empty_metadata)


class GeneratePodcastMetadata(TypedDict, total=False):
    """Metadata produced when creating a podcast generation job."""

    language: str
    type: str
    length: str
    format_args: dict[str, Any]


class TagPodcastMetadata(TypedDict, total=False):
    """Metadata recorded after tagging a podcast artifact."""

    tagged_at: str
    track: int
    total_tracks: int | None
    cover: str | None


class TranscribePodcastMetadata(TypedDict, total=False):
    """Metadata recorded after transcribing a podcast artifact."""

    transcribed_at: str
    model: str
    preprocessed: bool
    speed_factor: float
    language: str
    lrc_path: str


class PollArtifactTaskMetadata(TypedDict, total=False):
    """Metadata recorded when polling an artifact generation task."""

    task_id: str
    status: TaskStatus
    polled_at: str


PodcastArtifactMetadata = TypedDict(
    "PodcastArtifactMetadata",
    {
        "generate-podcast": NotRequired[GeneratePodcastMetadata],
        "track": NotRequired[int],
        "total_tracks": NotRequired[int],
        "tag-podcast": NotRequired[TagPodcastMetadata],
        "transcribe-podcast": NotRequired[TranscribePodcastMetadata],
        "poll-artifact-task": NotRequired[PollArtifactTaskMetadata],
    },
    total=False,
)
