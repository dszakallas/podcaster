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
    importers: List[Union[Ref["ImporterConfig"], "ImporterConfig"]] = Field(
        default_factory=list
    )


class ImporterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match: List[str] = Field(default_factory=lambda: [".*"])
    native: Optional[NativeImporterConfig] = None
    scraper: Optional[Union[Ref[ScraperConfig], ScraperConfig]] = None
    chain: Optional[ChainImporterConfig] = None


class EnrichWebConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = True
    retry_count: int = 0
    fallback_mechanism: Union[str, Ref[ImporterConfig], ImporterConfig] = "ignore"
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
    podcast_transcriber: Union[
        Ref[PodcastTranscriptionConfig], PodcastTranscriptionConfig
    ] = Field(default_factory=Ref[PodcastTranscriptionConfig])


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
    notifiers: List[Union[NotifierRef, NotifierConfig]] = Field(default_factory=list)


DistributionRef = Ref[DistributionConfig]


class TaggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = True
    spec: Optional[Union[Ref[PodcastTagsConfig], PodcastTagsConfig]] = Field(
        default_factory=lambda: Ref[PodcastTagsConfig](ref="default")
    )


class DeepDiveArticleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["deep_dive_article"] = "deep_dive_article"
    podcast_generator: Union[Ref[PodcastGenerationConfig], PodcastGenerationConfig] = (
        Field(default_factory=Ref[PodcastGenerationConfig])
    )
    importer: Union[Ref[ImporterConfig], ImporterConfig] = Field(
        default_factory=lambda: Ref[ImporterConfig](ref="default"),
    )
    enrich_web: EnrichWebConfig = Field(default_factory=EnrichWebConfig)
    generate_cover: GenerateCoverConfig = Field(default_factory=GenerateCoverConfig)
    transcribe: TranscribeConfig = Field(default_factory=TranscribeConfig)
    tagging: TaggingConfig = Field(default_factory=TaggingConfig)
    distribute: List[Union[DistributionRef, DistributionConfig]] = Field(
        default_factory=list
    )


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


class RefResolver:
    """Walks a Pydantic model tree and resolves all Ref instances in-place.

    Args:
        registries: Maps target type names to the config attribute path
            containing the registry dict. E.g. {"ImporterConfig": "importers"}.
    """

    def __init__(self, registries: dict[str, str]) -> None:
        self._registries = registries

    def resolve(self, config: BaseModel) -> BaseModel:
        """Resolve all Ref instances in the config tree, modifying it in-place."""
        cache: dict[int, Any] = {}
        visited: set[str] = set()
        self._walk(config, config, cache, visited)
        return config

    def _resolve_ref(
        self,
        ref: Ref,
        root: BaseModel,
        cache: dict[int, Any],
        visited: set[str],
    ) -> Any:
        cache_key = id(ref)
        if cache_key in cache:
            return cache[cache_key]

        key = f"{type(ref).__name__}:{ref.ref}"
        if key in visited:
            raise ValueError(f"Circular reference detected: {key}")

        metadata = getattr(type(ref), "__pydantic_generic_metadata__", None)
        if not metadata or not metadata.get("args"):
            raise ValueError(f"Cannot determine target type for {type(ref)}")
        target_type = metadata["args"][0]
        target_name = (
            target_type if isinstance(target_type, str) else target_type.__name__
        )
        field_name = self._registries.get(target_name)
        if not field_name:
            raise ValueError(f"No registry mapping for target type {target_type}")

        registry = getattr(root, field_name)
        name = ref.ref
        if name and name not in registry:
            raise ValueError(f"'{name}' not found in config.{field_name}")
        target = registry[name] if name else None

        cache[cache_key] = target

        visited.add(key)
        if target is not None:
            self._walk(target, root, cache, visited)
        visited.discard(key)

        return target

    def _walk(
        self,
        obj: Any,
        root: BaseModel,
        cache: dict[int, Any],
        visited: set[str],
    ) -> None:
        if isinstance(obj, BaseModel):
            updates: dict[str, Any] = {}
            for field_name in type(obj).model_fields:
                val = getattr(obj, field_name)
                if isinstance(val, Ref):
                    updates[field_name] = self._resolve_ref(val, root, cache, visited)
                elif isinstance(val, list):
                    resolved_list: list[Any] = []
                    changed = False
                    for item in val:
                        if isinstance(item, Ref):
                            resolved_list.append(
                                self._resolve_ref(item, root, cache, visited)
                            )
                            changed = True
                        else:
                            resolved_list.append(item)
                            if isinstance(item, BaseModel):
                                self._walk(item, root, cache, visited)
                    if changed:
                        updates[field_name] = resolved_list
                elif isinstance(val, dict):
                    resolved_dict: dict[Any, Any] = {}
                    changed = False
                    for k, v in val.items():
                        if isinstance(v, Ref):
                            resolved_dict[k] = self._resolve_ref(
                                v, root, cache, visited
                            )
                            changed = True
                        else:
                            resolved_dict[k] = v
                            if isinstance(v, BaseModel):
                                self._walk(v, root, cache, visited)
                    if changed:
                        updates[field_name] = resolved_dict
                elif isinstance(val, BaseModel):
                    self._walk(val, root, cache, visited)
            for k, v in updates.items():
                setattr(obj, k, v)
        elif isinstance(obj, dict):
            for k, v in list(obj.items()):
                if isinstance(v, Ref):
                    obj[k] = self._resolve_ref(v, root, cache, visited)
                elif isinstance(v, BaseModel):
                    self._walk(v, root, cache, visited)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, Ref):
                    obj[i] = self._resolve_ref(item, root, cache, visited)
                elif isinstance(item, BaseModel):
                    self._walk(item, root, cache, visited)


_APP_CONFIG_REGISTRIES = {
    "ImporterConfig": "importers",
    "ScraperConfig": "scrapers",
    "PodcastGenerationConfig": "podcast_generators",
    "PodcastTranscriptionConfig": "podcast_transcribers",
    "PodcastTagsConfig": "podcast_tags",
    "NotifierConfig": "notifiers",
    "DistributionConfig": "distributions",
}


def resolve_refs(config: "AppConfig") -> "AppConfig":
    """Resolve all Ref instances in an AppConfig tree."""
    resolver = RefResolver(_APP_CONFIG_REGISTRIES)
    resolver.resolve(config)
    return config
