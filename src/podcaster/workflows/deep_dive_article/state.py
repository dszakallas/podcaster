from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

from podcaster.models import TaskStatus


class TranscriptionState(BaseModel):
    status: TaskStatus = TaskStatus.PENDING
    task_id: Optional[str] = None
    gcs_uri: Optional[str] = None
    lrc_path: Optional[str] = None
    error: Optional[str] = None


class TaskState(BaseModel):
    task_id: str
    language: str
    status: TaskStatus = TaskStatus.PENDING
    title: Optional[str] = None
    audio_path: Optional[str] = None
    lrc_path: Optional[str] = None
    is_tagged: bool = False
    error: Optional[str] = None
    generation_started_at: Optional[float] = None
    transcription: List[TranscriptionState] = Field(default_factory=list)


class WorkflowConfig(BaseModel):
    length: Optional[str] = None
    languages: List[str] = Field(default_factory=list)
    enrich_web: bool = True
    generate_cover: bool = True
    transcribe: bool = False


class EnrichmentState(BaseModel):
    status: TaskStatus = TaskStatus.PENDING
    task_id: Optional[str] = None
    topic: Optional[str] = None
    summary: Optional[str] = None
    suggested_length: Optional[str] = None
    error: Optional[str] = None


class CoverState(BaseModel):
    status: TaskStatus = TaskStatus.PENDING
    task_id: Optional[str] = None
    image_gen_prompt: Optional[str] = None
    error: Optional[str] = None


class WorkflowState(BaseModel):
    version: int = 1
    notebook_id: str
    notebook_title: Optional[str] = None
    preset: str
    status: TaskStatus = TaskStatus.IN_PROGRESS
    config: WorkflowConfig
    source_id: Optional[str] = None
    cover_image_path: Optional[str] = None
    enrichment: List[EnrichmentState] = Field(default_factory=list)
    cover: List[CoverState] = Field(default_factory=list)
    tasks: List[TaskState] = Field(default_factory=list)

    def save(self, directory: Path) -> None:
        """Saves the workflow state to state.json in the specified directory."""
        state_file = directory / "state.json"
        state_file.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, directory: Path) -> Optional["WorkflowState"]:
        """Loads the workflow state from state.json in the specified directory if it exists."""
        state_file = directory / "state.json"
        if not state_file.exists():
            return None
        try:
            return cls.model_validate_json(state_file.read_text(encoding="utf-8"))
        except Exception:
            return None
