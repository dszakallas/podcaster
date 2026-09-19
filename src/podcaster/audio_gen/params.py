from pydantic import BaseModel, ConfigDict, Field


class AudioGenParams(BaseModel):
    notebook_id: str
    length: str


class StandardPodcastTaskInputs(BaseModel):
    """Standardized input parameters across all podcast generation tasks."""

    model_config = ConfigDict(extra="ignore")
    topic: str | None = None
    focus: str | None = None
    source_id: str | None = None
    roles: list[str] | None = Field(default=None, min_length=2, max_length=2)
    agenda: str | None = None
