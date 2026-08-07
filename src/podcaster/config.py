from typing import (
    Any,
    Dict,
    Generic,
    List,
    Literal,
    Optional,
    TypeVar,
    Union,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    TypeAdapter,
    ValidationError,
    model_validator,
)

T = TypeVar("T", bound=BaseModel)


class Ref(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="allow")
    ref: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _parse(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"ref": data}
        return data

    @model_validator(mode="after")
    def _validate_extra(self) -> "Ref[T]":
        metadata = getattr(self.__class__, "__pydantic_generic_metadata__", None)
        if not metadata or not metadata.get("args"):
            return self
        T_type = metadata["args"][0]
        if not isinstance(T_type, type) or not issubclass(T_type, BaseModel):
            return self

        if self.model_extra:
            errors = []
            validated_extra = {}
            for name, val in self.model_extra.items():
                if name not in T_type.model_fields:
                    if T_type.model_config.get("extra") == "forbid":
                        errors.append(
                            {
                                "type": "extra_forbidden",
                                "loc": (name,),
                                "input": val,
                                "msg": "Extra inputs are not permitted",
                            }
                        )
                    continue

                field_info = T_type.model_fields[name]
                try:
                    adapter = TypeAdapter(field_info.annotation)
                    validated_extra[name] = adapter.validate_python(val)
                except ValidationError as e:
                    for err in e.errors():
                        err_loc = (name,) + err["loc"]
                        errors.append(
                            {
                                "type": err["type"],
                                "loc": err_loc,
                                "input": err["input"],
                                "msg": err["msg"],
                            }
                        )

            if errors:
                raise ValidationError.from_exception_data(
                    self.__class__.__name__, errors
                )

            self.model_extra.clear()
            self.model_extra.update(validated_extra)

        return self

    def __getattr__(self, name: str) -> Any:
        if self.model_extra and name in self.model_extra:
            return self.model_extra[name]
        metadata = getattr(self.__class__, "__pydantic_generic_metadata__", None)
        if metadata and metadata.get("args"):
            T_type = metadata["args"][0]
            # Resolve forward-reference strings
            if isinstance(T_type, str):
                import sys

                T_type = sys.modules[__name__].__dict__.get(T_type)
            if (
                isinstance(T_type, type)
                and issubclass(T_type, BaseModel)
                and name in T_type.model_fields
            ):
                return None
        raise AttributeError(
            f"'{self.__class__.__name__}' object has no attribute '{name}'"
        )


class PodcastGenerationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    languages: List[str] = Field(default_factory=lambda: ["en"])
    length: Literal["short", "default", "long", "auto"] = "default"
    ignore_errors: bool = False


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


class ScraperConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = "playwright"
    agent: Optional[ScraperAgentConfig] = None


class ChainImporterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    importers: List[Ref["ImporterConfig"]] = Field(default_factory=list)


class ImporterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match: List[str] = Field(default_factory=lambda: [".*"])
    native: Optional[NativeImporterConfig] = None
    scraper: Optional[Ref[ScraperConfig]] = None
    chain: Optional[ChainImporterConfig] = None


class EnrichWebConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = True
    retry_count: int = 0
    fallback_mechanism: Union[str, Ref[ImporterConfig]] = "ignore"
    spec: EnrichWebSpecConfig = Field(default_factory=EnrichWebSpecConfig)


class GenerateCoverSpecConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerateCoverConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = True
    retry_count: int = 0
    spec: GenerateCoverSpecConfig = Field(default_factory=GenerateCoverSpecConfig)


class PodcastTranscriptionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speed_factor: float = 1.5
    languages: List[str] = Field(default_factory=lambda: [])


class TranscribeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = False
    retry_count: int = 0
    podcast_transcriber: Ref[PodcastTranscriptionConfig] = Field(
        default_factory=Ref[PodcastTranscriptionConfig]
    )


class RsyncDistributionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["rsync", "rclone"] = "rsync"
    destination: str
    flags: List[str] = Field(default_factory=list)


class PlexNotifierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section_id: Union[int, str]
    server_library_path: Optional[str] = None
    server_url: Optional[str] = None
    token: Optional[str] = None


class DiscordNotifierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    webhook_url: Optional[str] = None
    bot_token: Optional[str] = None
    channel_id: Optional[Union[int, str]] = None


class NotifierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plex: Optional[PlexNotifierConfig] = None
    discord: Optional[DiscordNotifierConfig] = None


NotifierRef = Ref[NotifierConfig]


class DistributionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rsync: Optional[RsyncDistributionConfig] = None
    notifiers: List[NotifierRef] = Field(default_factory=list)


DistributionRef = Ref[DistributionConfig]


class TaggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = True
    spec: Optional[Ref[PodcastTagsConfig]] = Field(
        default_factory=lambda: Ref[PodcastTagsConfig](ref="default")
    )


class DeepDiveArticleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["deep_dive_article"] = "deep_dive_article"
    podcast_generator: Ref[PodcastGenerationConfig] = Field(
        default_factory=Ref[PodcastGenerationConfig]
    )
    importer: Ref[ImporterConfig] = Field(
        default_factory=lambda: Ref[ImporterConfig](ref="default"),
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


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str
    args: List[str] = Field(default_factory=list)


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
                        Ref[ImporterConfig](ref="native"),
                        Ref[ImporterConfig](ref="scraper"),
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
                scraper=Ref[ScraperConfig](ref="default"),
            ),
        },
    )
    podcast_tags: Dict[str, PodcastTagsConfig] = Field(
        default_factory=lambda: {"default": PodcastTagsConfig()}
    )
    workflow: WorkflowConfig = Field(default_factory=lambda: WorkflowConfig(root={}))
    notifiers: Dict[str, NotifierConfig] = Field(
        default_factory=dict,
    )
    distributions: Dict[str, DistributionConfig] = Field(
        default_factory=dict,
    )
    gcp: GCPConfig = Field(default_factory=GCPConfig)


ChainImporterConfig.model_rebuild()
ImporterConfig.model_rebuild()
