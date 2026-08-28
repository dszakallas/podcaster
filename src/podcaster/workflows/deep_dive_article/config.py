"""Configuration for the deep-dive article workflow."""

from typing import List, Literal

from pydantic import BaseModel, ConfigDict

from podcaster.config import (
    DistributionConfig,
    EnrichWebConfig,
    GenerateCoverConfig,
    ImporterConfig,
    MaybeRef,
    PodcastGenerationConfig,
    TaggingConfig,
    TranscribeConfig,
)


class DeepDiveArticleConfig(BaseModel):
    """Configuration for a deep-dive article workflow preset."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["deep_dive_article"]
    podcast_generator: MaybeRef[PodcastGenerationConfig]
    importer: MaybeRef[ImporterConfig]
    enrich_web: EnrichWebConfig
    generate_cover: GenerateCoverConfig
    transcribe: TranscribeConfig
    tagging: TaggingConfig
    distribute: List[MaybeRef[DistributionConfig]]
