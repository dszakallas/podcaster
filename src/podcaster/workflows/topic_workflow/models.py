"""Data models and recipe schema for the topic workflow."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ResearchDescriptor(BaseModel):
    """Research parameters for topic-based source discovery."""

    model_config = ConfigDict(extra="forbid")
    query: str
    mode: Literal["fast", "deep"] = "fast"


class TopicPodcastBase(BaseModel):
    """Base fields shared by all topic-driven podcast generator items."""

    model_config = ConfigDict(extra="forbid")
    focus: str | None = None
    agenda: str | None = None
    roles: list[str] | None = Field(default=None, min_length=2, max_length=2)
    languages: list[str] | None = None
    length: Literal["short", "default", "long", "auto"] | None = None


class TopicDeepDiveDescriptor(TopicPodcastBase):
    """Specification for a deep dive conversation unpacking and connecting topic sources."""

    type: Literal["TopicDeepDive"] = "TopicDeepDive"


class TopicDebateDescriptor(TopicPodcastBase):
    """Specification for a debate illuminating contrasting viewpoints on topic sources."""

    type: Literal["TopicDebate"] = "TopicDebate"


class TopicArticleDescriptor(TopicPodcastBase):
    """Specification for an interview with the author of a feature article source."""

    type: Literal["TopicArticle"] = "TopicArticle"
    source_id: str | None = None


TopicGeneratorDescriptor = Annotated[
    TopicDeepDiveDescriptor | TopicDebateDescriptor | TopicArticleDescriptor,
    Field(discriminator="type"),
]


class TopicWorkflowRecipe(BaseModel):
    """Dynamic recipe describing the research query and podcast generation matrix."""

    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    research: ResearchDescriptor
    podcasts: list[TopicGeneratorDescriptor] = Field(min_length=1)
