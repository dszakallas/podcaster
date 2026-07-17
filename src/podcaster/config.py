from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, RootModel


class PodcastGenerationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    languages: List[str] = Field(default_factory=lambda: ["en"])
    length: Literal["short", "default", "long", "auto"] = "default"


class PodcastTagsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    album_artist: str = "Your Name"
    artists: List[str] = Field(default_factory=lambda: ["Your Name"])


class EnrichWebSpecConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_imports: int = 5
    mode: Literal["fast", "deep"] = "fast"
    ignore_errors: bool = False


class EnrichWebConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = True
    retry_count: int = 0
    spec: EnrichWebSpecConfig = Field(default_factory=EnrichWebSpecConfig)


class GenerateCoverSpecConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerateCoverConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = True
    retry_count: int = 0
    spec: GenerateCoverSpecConfig = Field(default_factory=GenerateCoverSpecConfig)


class PodcastTranscriberRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: Optional[str] = None
    # Optional inline config overrides
    speed_factor: Optional[float] = None
    languages: Optional[List[str]] = None


class TranscribeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = False
    retry_count: int = 0
    podcast_transcriber: PodcastTranscriberRef = Field(
        default_factory=PodcastTranscriberRef
    )


class RsyncTargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["rsync", "rclone"] = "rsync"
    destination: str
    rclone_flags: List[str] = Field(default_factory=list)


class PlexRsyncConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    ref: Optional[str] = None
    # Optional inline config if ref is not used
    method: Optional[Literal["rsync", "rclone"]] = None
    destination: Optional[str] = None
    rclone_flags: Optional[List[str]] = None


class PlexTargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rsync: Optional[PlexRsyncConfig] = None
    section_id: int
    server_library_path: str
    server_url: Optional[str] = None
    token: Optional[str] = None


class DistributionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["rsync", "plex"]
    ref: Optional[str] = None
    # Optional inline configurations
    method: Optional[Literal["rsync", "rclone"]] = None
    destination: Optional[str] = None
    section_id: Optional[int] = None
    server_library_path: Optional[str] = None
    rclone_flags: Optional[List[str]] = None


class PodcastGeneratorRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: Optional[str] = None
    # Optional inline config if ref is not used
    languages: Optional[List[str]] = None
    length: Optional[Literal["short", "default", "long", "auto"]] = None


class DeepDiveArticleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["deep_dive_article"] = "deep_dive_article"
    podcast_generator: PodcastGeneratorRef = Field(default_factory=PodcastGeneratorRef)
    enrich_web: EnrichWebConfig = Field(default_factory=EnrichWebConfig)
    generate_cover: GenerateCoverConfig = Field(default_factory=GenerateCoverConfig)
    transcribe: TranscribeConfig = Field(default_factory=TranscribeConfig)
    distribute: List[DistributionTarget] = Field(default_factory=list)


class WorkflowConfig(RootModel):
    root: Dict[str, DeepDiveArticleConfig]


class GCPConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: Optional[str] = None
    location: str = "us-central1"
    gcs_bucket: Optional[str] = None


class ResearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unimportables: List[str] = Field(default_factory=list)
    import_fallback: Literal["ignore", "force", "scrape"] = "scrape"


class ScraperAgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: Optional[str] = None
    command: Optional[str] = None
    args: Optional[List[str]] = None


class ScraperConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = "playwright"
    agent: Optional[ScraperAgentConfig] = None


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str
    args: List[str] = Field(default_factory=list)


class PodcastTranscriptionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speed_factor: float = 1.5
    languages: List[str] = Field(default_factory=lambda: [])


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    podcast_dir: str = "podcasts"
    scraper: ScraperConfig = Field(default_factory=ScraperConfig)
    agents: Dict[str, AgentConfig] = Field(default_factory=dict)
    podcast_generators: Dict[str, PodcastGenerationConfig] = Field(
        default_factory=lambda: {"default": PodcastGenerationConfig()}
    )
    podcast_transcribers: Dict[str, PodcastTranscriptionConfig] = Field(
        default_factory=lambda: {"default": PodcastTranscriptionConfig()}
    )
    podcast_tags: PodcastTagsConfig = Field(default_factory=PodcastTagsConfig)
    workflow: WorkflowConfig = Field(default_factory=lambda: WorkflowConfig(root={}))
    rsync: Dict[str, RsyncTargetConfig] = Field(default_factory=dict)
    plex: Dict[str, PlexTargetConfig] = Field(default_factory=dict)
    gcp: GCPConfig = Field(default_factory=GCPConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
