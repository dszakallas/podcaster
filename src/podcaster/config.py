from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    model_validator,
)


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


class NativeImporterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScraperAgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: Optional[str] = None
    command: Optional[str] = None
    args: Optional[List[str]] = None


class ScraperRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: Optional[str] = None
    tool: Optional[str] = None
    agent: Optional[ScraperAgentConfig] = None


class ImporterItemConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: Optional[str] = None
    match: Optional[List[str]] = None
    native: Optional[NativeImporterConfig] = None
    scraper: Optional[ScraperRef] = None
    chain: Optional["ChainImporterConfig"] = None


class ChainImporterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    importers: List["ImporterRef"] = Field(default_factory=list)


class ImporterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match: List[str] = Field(default_factory=lambda: [".*"])
    native: Optional[NativeImporterConfig] = None
    scraper: Optional[ScraperRef] = None
    chain: Optional[ChainImporterConfig] = None


class ImporterRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: Optional[str] = None
    match: Optional[List[str]] = None
    native: Optional[NativeImporterConfig] = None
    scraper: Optional[ScraperRef] = None
    chain: Optional[ChainImporterConfig] = None

    @model_validator(mode="before")
    @classmethod
    def _parse_string(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"ref": data}
        return data


ImporterItemConfig.model_rebuild()
ChainImporterConfig.model_rebuild()


class EnrichWebConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = True
    retry_count: int = 0
    fallback_mechanism: Union[str, ImporterRef] = "ignore"
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


class RsyncDistributionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["rsync", "rclone"] = "rsync"
    destination: str
    flags: List[str] = Field(default_factory=list)


class PlexRsyncSpecConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: Optional[str] = None
    method: Optional[Literal["rsync", "rclone"]] = None
    destination: Optional[str] = None
    flags: Optional[List[str]] = None

    @model_validator(mode="before")
    @classmethod
    def _parse_string(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"ref": data}
        return data


class PlexRsyncConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = True
    spec: Optional[PlexRsyncSpecConfig] = None


class PlexDistributionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section_id: int
    server_library_path: str
    server_url: Optional[str] = None
    token: Optional[str] = None
    rsync: Optional[PlexRsyncConfig] = None


class DistributionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rsync: Optional[RsyncDistributionConfig] = None
    plex: Optional[PlexDistributionConfig] = None


class RsyncDistributionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: Optional[str] = None
    method: Optional[Literal["rsync", "rclone"]] = None
    destination: Optional[str] = None
    flags: Optional[List[str]] = None

    @model_validator(mode="before")
    @classmethod
    def _parse_string(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"ref": data}
        return data


class PlexDistributionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: Optional[str] = None
    section_id: Optional[int] = None
    server_library_path: Optional[str] = None
    server_url: Optional[str] = None
    token: Optional[str] = None
    rsync: Optional[PlexRsyncConfig] = None

    @model_validator(mode="before")
    @classmethod
    def _parse_string(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"ref": data}
        return data


class DistributionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: Optional[str] = None
    rsync: Optional[Union[RsyncDistributionRef, str]] = None
    plex: Optional[Union[PlexDistributionRef, str]] = None

    @model_validator(mode="before")
    @classmethod
    def _parse_string(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"ref": data}
        return data


class PodcastGeneratorRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: Optional[str] = None
    # Optional inline config if ref is not used
    languages: Optional[List[str]] = None
    length: Optional[Literal["short", "default", "long", "auto"]] = None


class TaggingSpecConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: Optional[str] = None
    album_artist: Optional[str] = None
    artists: Optional[List[str]] = None

    @model_validator(mode="before")
    @classmethod
    def _parse_string(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"ref": data}
        return data


class TaggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = True
    spec: Optional[TaggingSpecConfig] = Field(
        default_factory=lambda: TaggingSpecConfig(ref="default")
    )


class DeepDiveArticleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["deep_dive_article"] = "deep_dive_article"
    podcast_generator: PodcastGeneratorRef = Field(default_factory=PodcastGeneratorRef)
    importer: ImporterRef = Field(
        default_factory=lambda: ImporterRef(ref="default"),
    )
    enrich_web: EnrichWebConfig = Field(default_factory=EnrichWebConfig)
    generate_cover: GenerateCoverConfig = Field(default_factory=GenerateCoverConfig)
    transcribe: TranscribeConfig = Field(default_factory=TranscribeConfig)
    tagging: TaggingConfig = Field(default_factory=TaggingConfig)
    distribute: List[DistributionRef] = Field(default_factory=list)


class WorkflowConfig(RootModel):
    root: Dict[str, DeepDiveArticleConfig]


class GCPConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: Optional[str] = None
    location: str = "us-central1"
    gcs_bucket: Optional[str] = None


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
    scrapers: Dict[str, ScraperConfig] = Field(
        default_factory=lambda: {"default": ScraperConfig(tool="playwright")}
    )
    agents: Dict[str, AgentConfig] = Field(default_factory=dict)
    podcast_generators: Dict[str, PodcastGenerationConfig] = Field(
        default_factory=lambda: {"default": PodcastGenerationConfig()}
    )
    podcast_transcribers: Dict[str, PodcastTranscriptionConfig] = Field(
        default_factory=lambda: {"default": PodcastTranscriptionConfig()}
    )
    importers: Dict[str, ImporterConfig] = Field(
        default_factory=lambda: {
            "default": ImporterConfig(
                chain=ChainImporterConfig(
                    importers=[
                        ImporterRef(ref="native"),
                        ImporterRef(ref="scraper"),
                    ]
                )
            ),
            "native": ImporterConfig(
                match=[
                    ".*",
                    "!https?://.*wsj\\.com/.*",
                    "!https?://.*forbes\\.com/.*",
                    "!https?://.*politico\\.eu/.*",
                    "!https?://.*nytimes\\.com/.*",
                    "!https?://.*hvg\\.hu/.*",
                    "!https?://.*msn\\.com/.*",
                    "!https?://.*archive\\.(ph|is)/.*",
                ],
                native=NativeImporterConfig(),
            ),
            "scraper": ImporterConfig(
                match=["https?://.*", "!file:.*", "!gdrive:.*"],
                scraper=ScraperRef(ref="default"),
            ),
        },
    )
    podcast_tags: Dict[str, PodcastTagsConfig] = Field(
        default_factory=lambda: {"default": PodcastTagsConfig()}
    )
    workflow: WorkflowConfig = Field(default_factory=lambda: WorkflowConfig(root={}))
    distributions: Dict[str, DistributionConfig] = Field(
        default_factory=dict,
    )
    gcp: GCPConfig = Field(default_factory=GCPConfig)
