"""Static configuration for topic workflow presets."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from podcaster.config import (
    DistributionConfig,
    EnrichWebConfig,
    GenerateCoverConfig,
    MaybeRef,
    PodcastGenerationConfig,
    TaggingConfig,
    TranscribeConfig,
)


class TopicWorkflowConfig(BaseModel):
    """Static configuration for a topic workflow preset."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["topic_workflow"]
    podcast_generator: MaybeRef[PodcastGenerationConfig]
    enrich_web: EnrichWebConfig
    generate_cover: GenerateCoverConfig
    transcribe: TranscribeConfig
    tagging: TaggingConfig
    distribute: list[MaybeRef[DistributionConfig]]
