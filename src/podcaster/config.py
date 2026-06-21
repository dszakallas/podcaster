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


class EnrichWebConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = True
    max_imports: int = 5
    mode: Literal["fast", "deep"] = "fast"


class GenerateCoverConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = True


class TranscribeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = False
    languages: List[str] = Field(default_factory=lambda: [])


class RsyncTargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["rsync", "rclone"] = "rsync"
    destination: str


class PlexRsyncConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    ref: Optional[str] = None
    # Optional inline config if ref is not used
    method: Optional[Literal["rsync", "rclone"]] = None
    destination: Optional[str] = None


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


class DeepDiveArticleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["deep_dive_article"] = "deep_dive_article"
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


class ScraperConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = "playwright"
    ref: Optional[str] = None
    command: Optional[str] = None
    args: Optional[List[str]] = None


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str
    args: List[str] = Field(default_factory=list)


class PodcastTranscriptionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speed_factor: float = 1.5


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    podcast_dir: str = "podcasts"
    scraper: ScraperConfig = Field(default_factory=ScraperConfig)
    agents: Dict[str, AgentConfig] = Field(default_factory=dict)
    podcast_generation: PodcastGenerationConfig = Field(
        default_factory=PodcastGenerationConfig
    )
    podcast_transcription: PodcastTranscriptionConfig = Field(
        default_factory=PodcastTranscriptionConfig
    )
    podcast_tags: PodcastTagsConfig = Field(default_factory=PodcastTagsConfig)
    workflow: WorkflowConfig = Field(default_factory=lambda: WorkflowConfig(root={}))
    rsync: Dict[str, RsyncTargetConfig] = Field(default_factory=dict)
    plex: Dict[str, PlexTargetConfig] = Field(default_factory=dict)
    gcp: GCPConfig = Field(default_factory=GCPConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
