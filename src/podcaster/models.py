from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class BaseTask(BaseModel):
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    error: Optional[str] = None
    type: str = ""


class PodcastGenTask(BaseTask):
    type: str = "podcast_gen"
    notebook_id: str
    title: Optional[str] = None
    eta: float = 10.0
    generation_started_at: Optional[float] = None
    created_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PodcastGenArtifact(BaseModel):
    notebook_id: str
    artifact_id: str
    title: str
    path: str
    filename: str
    lrc_path: Optional[str] = None
    transcript_path: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


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
    imported: List[Dict[str, Any]] = Field(default_factory=list)


class CoverTask(BaseTask):
    type: str = "cover"
    notebook_id: str
    image_gen_prompt: str
    cover_path: Optional[str] = None


class TranscriptionTask(BaseTask):
    type: str = "transcription"
    artifact_id: str
    path: str
    gcs_uri: Optional[str] = None
    preprocessed_path: Optional[str] = None
    bcp47_lang: str = "en-US"
    speed_factor: float = 1.0
    lrc_path: Optional[str] = None
    transcript_path: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
