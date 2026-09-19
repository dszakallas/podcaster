import contextlib
import os
import re
import types
from collections.abc import Callable
from typing import (
    Annotated,
    Any,
    ForwardRef,
    Literal,
    TypeVar,
    Union,
    get_args,
    get_origin,
)

import yaml
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


def _reconstruct_ref(data: dict) -> "Ref[Any]":
    return Ref(**data)


class Ref[T](BaseModel):
    model_config = ConfigDict(extra="allow")
    ref: str | None = None

    def __reduce__(self):
        data = {"ref": self.ref}
        if self.model_extra:
            data.update(self.model_extra)
        return (_reconstruct_ref, (data,))

    @model_validator(mode="before")
    @classmethod
    def _parse(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"ref": data}
        return data

    @model_validator(mode="after")  # pyright: ignore[reportGeneralTypeIssues]
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


MaybeRef = Ref[T] | T


class PodcastGenerationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = "main-article-with-author"
    format_args: dict[str, Any] = Field(default_factory=dict)
    languages: list[str] = Field(default_factory=lambda: ["en"])
    length: Literal["short", "default", "long", "auto"] = "default"
    ignore_errors: bool = False


class PodcastTagsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    album_artist: str = "Your Name"
    artists: list[str] = Field(default_factory=lambda: ["Your Name"])


class EnrichWebSpecConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["fast", "deep"] = "fast"
    fallback_importer: MaybeRef["ImporterConfig"] | None = None
    max_import_failures: int | None = None


class NativeImporterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str
    args: list[str] = Field(default_factory=list)
    timeout: float | None = Field(default=None, gt=0)


class ScraperConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = "playwright"
    agent: MaybeRef[AgentConfig]
    timeout: float | None = Field(default=None, gt=0)


class ChainImporterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    importers: list[MaybeRef["ImporterConfig"]] = Field(default_factory=list)


class ImporterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match: list[str] = Field(default_factory=lambda: [".*"])
    native: NativeImporterConfig | None = None
    scraper: MaybeRef[ScraperConfig] | None = None
    chain: ChainImporterConfig | None = None


class EnrichWebConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = True
    retry_count: int = 0
    spec: EnrichWebSpecConfig = Field(default_factory=EnrichWebSpecConfig)


DEFAULT_COVER_MODEL = "gemini-3.1-flash-image"


class GenerateCoverSpecConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = DEFAULT_COVER_MODEL


class GenerateCoverConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = True
    retry_count: int = 0
    spec: GenerateCoverSpecConfig = Field(default_factory=GenerateCoverSpecConfig)


class PodcastTranscriptionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speed_factor: float = 1.5
    languages: list[str] = Field(default_factory=lambda: [])


class TranscribeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = False
    retry_count: int = 0
    podcast_transcriber: MaybeRef[PodcastTranscriptionConfig]


class RsyncDistributionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["rsync", "rclone"] = "rsync"
    destination: str
    flags: list[str] = Field(default_factory=list)
    filename_template: str = (
        "{{ notebook.creation_date }} - {{ notebook.title }} [nlm_{{ notebook.id }}]"
        "/{{ artifact.name }} [{{ artifact.id }}]"
    )


class PlexNotifierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section_id: int | str
    server_library_path: Annotated[
        str | None, Field(json_schema_extra={"env_var": True})
    ] = None
    server_url: Annotated[str | None, Field(json_schema_extra={"env_var": True})] = None
    token: Annotated[str | None, Field(json_schema_extra={"env_var": True})] = None


class DiscordNotifierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    webhook_url: Annotated[str | None, Field(json_schema_extra={"env_var": True})] = (
        None
    )
    bot_token: Annotated[str | None, Field(json_schema_extra={"env_var": True})] = None
    channel_id: Annotated[
        int | str | None,
        Field(json_schema_extra={"env_var": True}),
    ] = None


class NotifierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plex: PlexNotifierConfig | None = None
    discord: DiscordNotifierConfig | None = None


class DistributionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rsync: RsyncDistributionConfig | None = None
    notifiers: list[MaybeRef[NotifierConfig]] = Field(default_factory=list)


class TaggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = True
    spec: MaybeRef[PodcastTagsConfig]


WorkflowConfigParser = Callable[[str, dict[str, Any]], BaseModel]
_WORKFLOW_CONFIG_PARSER: WorkflowConfigParser | None = None


def set_workflow_config_parser(parser: WorkflowConfigParser | None) -> None:
    """Register or override the workflow configuration parser."""
    global _WORKFLOW_CONFIG_PARSER
    _WORKFLOW_CONFIG_PARSER = parser


def _default_workflow_config_parser(
    workflow_type: str, raw_config: dict[str, Any]
) -> BaseModel:
    from podcaster.workflows import get_workflow_plugin

    plugin = get_workflow_plugin(workflow_type)
    if plugin is None:
        raise ValueError(f"Unknown workflow type '{workflow_type}'.")
    return plugin.config_type.model_validate(raw_config)


class WorkflowPresetsConfig(RootModel[dict[str, BaseModel]]):
    """Workflow presets parsed by their declared workflow plugin."""

    @model_validator(mode="before")
    @classmethod
    def _parse_workflow_plugins(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        parser = _WORKFLOW_CONFIG_PARSER or _default_workflow_config_parser

        parsed: dict[str, BaseModel] = {}
        for preset_name, raw_config in data.items():
            if isinstance(raw_config, BaseModel):
                raw_config = raw_config.model_dump()
            if not isinstance(raw_config, dict):
                raise ValueError(
                    f"Workflow preset '{preset_name}' must be a configuration mapping."
                )

            workflow_type = raw_config.get("type")
            if not isinstance(workflow_type, str):
                raise ValueError(
                    f"Workflow preset '{preset_name}' must declare a string 'type'."
                )

            try:
                parsed[preset_name] = parser(workflow_type, raw_config)
            except ValueError as e:
                raise ValueError(
                    f"Unknown workflow type '{workflow_type}' for preset '{preset_name}'."
                ) from e

        return parsed


class WorkflowConfig(BaseModel):
    """Workflow defaults and named workflow presets."""

    model_config = ConfigDict(extra="forbid")
    workdir: str = "."
    presets: WorkflowPresetsConfig


class GCPConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str | None = None
    location: str = "us-central1"
    gcs_bucket: str | None = None


class DBOSConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: str = "sqlite"
    sqlite_path: str = "~/.podcaster/dbos.db"
    postgres_url: str | None = None


class NotebookLMConfig(BaseModel):
    """Optional settings used to locate a NotebookLM browser profile."""

    model_config = ConfigDict(extra="forbid")
    home: Annotated[str | None, Field(json_schema_extra={"env_var": True})] = None
    storage_state: Annotated[str | None, Field(json_schema_extra={"env_var": True})] = (
        None
    )
    profile: Annotated[str | None, Field(json_schema_extra={"env_var": True})] = None


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dbos: DBOSConfig
    scrapers: dict[str, ScraperConfig]
    agents: dict[str, AgentConfig]
    podcast_generators: dict[str, PodcastGenerationConfig]
    podcast_transcribers: dict[str, PodcastTranscriptionConfig]
    importers: dict[str, ImporterConfig]
    podcast_tags: dict[str, PodcastTagsConfig]
    workflow: WorkflowConfig
    notifiers: dict[str, NotifierConfig]
    distributions: dict[str, DistributionConfig]
    gcp: GCPConfig
    notebooklm: NotebookLMConfig = Field(default_factory=NotebookLMConfig)


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

    @staticmethod
    def _target_name_from_union(args: tuple[Any, ...]) -> str | None:
        """Find the target type name from union arguments (for Ref[T] | T)."""
        for arg in args:
            if isinstance(arg, type) and issubclass(arg, Ref):
                inner = get_args(arg)
                if inner:
                    t = inner[0]
                    return t if isinstance(t, str) else getattr(t, "__name__", None)

        for arg in args:
            if arg is type(None) or (isinstance(arg, type) and issubclass(arg, Ref)):
                continue
            if isinstance(arg, ForwardRef):
                return arg.__forward_arg__
            if isinstance(arg, str):
                return arg
            if isinstance(arg, type):
                return arg.__name__
        return None

    @classmethod
    def _ref_target_name(cls, annotation: Any) -> str | None:
        """Extract the target type name from a field annotation containing Ref[T] or MaybeRef[T].

        Pydantic strips generic params from Ref during validation, so we can't rely
        on Ref[T] having args. Instead, for Union types (MaybeRef = Ref[T] | T),
        we look for the non-Ref, non-None member — that's the target type.
        """
        origin = get_origin(annotation)
        args = get_args(annotation)

        # Direct Ref[T] with generic args
        if isinstance(annotation, type) and issubclass(annotation, Ref):
            if args:
                t = args[0]
                return t if isinstance(t, str) else getattr(t, "__name__", None)
            return None

        # Union (covers Optional, MaybeRef)
        if origin in (Union, types.UnionType):
            return cls._target_name_from_union(args)

        # List[X], Dict[K, X] — recurse into type args
        if args:
            for arg in args:
                result = cls._ref_target_name(arg)
                if result:
                    return result

        return None

    @staticmethod
    def _contains_ref(annotation: Any) -> bool:
        """Return whether an annotation contains a Ref at any nesting level."""
        if isinstance(annotation, type) and issubclass(annotation, Ref):
            return True
        return any(RefResolver._contains_ref(arg) for arg in get_args(annotation))

    def _resolve_ref(
        self,
        ref: Ref,
        root: BaseModel,
        cache: dict[int, Any],
        visited: set[str],
        target_name: str | None = None,
    ) -> Any:
        cache_key = id(ref)
        if cache_key in cache:
            return cache[cache_key]

        key = f"{type(ref).__name__}:{ref.ref}"
        if key in visited:
            raise ValueError(f"Circular reference detected: {key}")

        if not target_name:
            # Fallback: try instance metadata (works for manually constructed Ref[T])
            metadata = getattr(type(ref), "__pydantic_generic_metadata__", None)
            if metadata and metadata.get("args"):
                target_type = metadata["args"][0]
                target_name = (
                    target_type
                    if isinstance(target_type, str)
                    else target_type.__name__
                )
        if not target_name:
            raise ValueError(f"Cannot determine target type for {type(ref)}")

        field_name = self._registries.get(target_name)
        if not field_name:
            raise ValueError(f"No registry mapping for target type {target_name}")

        registry = getattr(root, field_name)
        name = ref.ref
        if name and name not in registry:
            raise ValueError(f"'{name}' not found in config.{field_name}")
        target = registry[name] if name else None

        if target is not None and isinstance(target, BaseModel):
            if ref.model_extra:
                target = target.model_copy(update=ref.model_extra, deep=True)
            try:
                object.__setattr__(target, "_ref_name", name)
                object.__setattr__(
                    target,
                    "_ref_path",
                    f"{field_name}.{name}" if name else None,
                )
                object.__setattr__(target, "_config_registry", field_name)
            except Exception:
                pass

        cache[cache_key] = target

        visited.add(key)
        if target is not None:
            self._walk(target, root, cache, visited)
        visited.discard(key)

        return target

    def _resolve_list_field(
        self,
        items: list[Any],
        root: BaseModel,
        cache: dict[int, Any],
        visited: set[str],
        ref_target: str | None,
    ) -> tuple[list[Any], bool]:
        resolved_list: list[Any] = []
        changed = False
        for item in items:
            if isinstance(item, Ref):
                resolved_list.append(
                    self._resolve_ref(
                        item, root, cache, visited, target_name=ref_target
                    )
                )
                changed = True
            else:
                resolved_list.append(item)
                if isinstance(item, BaseModel):
                    self._walk(item, root, cache, visited)
        return resolved_list, changed

    def _resolve_dict_field(
        self,
        items: dict[Any, Any],
        root: BaseModel,
        cache: dict[int, Any],
        visited: set[str],
        ref_target: str | None,
        field_name: str | None = None,
    ) -> tuple[dict[Any, Any], bool]:
        resolved_dict: dict[Any, Any] = {}
        changed = False
        for k, v in items.items():
            if isinstance(v, Ref):
                resolved_dict[k] = self._resolve_ref(
                    v, root, cache, visited, target_name=ref_target
                )
                changed = True
            else:
                resolved_dict[k] = v
                if isinstance(v, BaseModel):
                    if not hasattr(v, "_ref_name"):
                        with contextlib.suppress(Exception):
                            object.__setattr__(v, "_ref_name", str(k))
                            if field_name:
                                object.__setattr__(v, "_ref_path", f"{field_name}.{k}")
                                object.__setattr__(v, "_config_registry", field_name)
                    self._walk(v, root, cache, visited)
        return resolved_dict, changed

    def _walk_model(
        self,
        obj: BaseModel,
        root: BaseModel,
        cache: dict[int, Any],
        visited: set[str],
    ) -> None:
        updates: dict[str, Any] = {}
        for field_name in type(obj).model_fields:
            val = getattr(obj, field_name)
            annotation = type(obj).model_fields[field_name].annotation
            ref_target = self._ref_target_name(annotation)
            if isinstance(val, Ref):
                updates[field_name] = self._resolve_ref(
                    val, root, cache, visited, target_name=ref_target
                )
            elif isinstance(val, list):
                res_list, changed = self._resolve_list_field(
                    val, root, cache, visited, ref_target
                )
                if changed:
                    updates[field_name] = res_list
            elif isinstance(val, dict):
                res_dict, changed = self._resolve_dict_field(
                    val, root, cache, visited, ref_target, field_name
                )
                if changed:
                    updates[field_name] = res_dict
            elif isinstance(val, BaseModel):
                self._walk(val, root, cache, visited)
        for k, v in updates.items():
            setattr(obj, k, v)

    def _walk(
        self,
        obj: Any,
        root: BaseModel,
        cache: dict[int, Any],
        visited: set[str],
    ) -> None:
        if isinstance(obj, BaseModel):
            self._walk_model(obj, root, cache, visited)
        elif isinstance(obj, dict):
            for k, v in list(obj.items()):
                if isinstance(v, Ref):
                    obj[k] = self._resolve_ref(v, root, cache, visited)
                elif isinstance(v, BaseModel):
                    if not hasattr(v, "_ref_name"):
                        with contextlib.suppress(Exception):
                            object.__setattr__(v, "_ref_name", str(k))
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
    "AgentConfig": "agents",
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
    _load_environment_defaults(config)
    return config


def _load_environment_defaults(config: "AppConfig") -> None:
    """Fill annotated, unset configuration fields from environment variables."""
    visited: set[int] = set()

    def walk(
        value: Any,
        registry_key: str | None = None,
        config_key: str | None = None,
        field_path: tuple[str, ...] = (),
    ) -> None:
        if isinstance(value, BaseModel):
            value_id = id(value)
            if value_id in visited:
                return
            visited.add(value_id)
            own_registry = getattr(value, "_config_registry", None)
            if own_registry:
                model_registry = own_registry
                model_key = getattr(value, "_ref_name", None)
                model_path = ()
            else:
                model_registry = registry_key
                model_key = config_key
                model_path = field_path
            _load_model_environment(value, model_registry, model_key, model_path)
            for field_name, field_info in type(value).model_fields.items():
                is_reference_boundary = RefResolver._contains_ref(field_info.annotation)
                child_registry = None if is_reference_boundary else model_registry
                child_key = None if is_reference_boundary else model_key
                child_path = () if is_reference_boundary else model_path + (field_name,)
                walk(
                    getattr(value, field_name),
                    child_registry,
                    child_key,
                    child_path,
                )
        elif isinstance(value, dict):
            for item in value.values():
                walk(item, registry_key, config_key, field_path)
        elif isinstance(value, list):
            for item in value:
                walk(item, registry_key, config_key, field_path)

    walk(config)


def _load_model_environment(
    model: BaseModel,
    registry_key: str | None,
    config_key: str | None,
    field_path: tuple[str, ...],
) -> None:
    """Fill annotated, unset fields on one configuration model."""
    for field_name, field_info in type(model).model_fields.items():
        extra = field_info.json_schema_extra
        uses_environment = isinstance(extra, dict) and extra.get("env_var") is True
        if uses_environment and getattr(model, field_name) is None:
            env_var = _environment_name(field_path + (field_name,))
            value = _get_environment_value(registry_key, config_key, env_var)
            if value is not None:
                setattr(model, field_name, value)


def _environment_name(field_path: tuple[str, ...]) -> str:
    """Convert a configuration field path into an uppercase environment name."""
    return "_".join(field_path).upper()


def _get_environment_value(
    registry_key: str | None, config_key: str | None, env_var: str
) -> str | None:
    """Look up a registry-scoped environment variable before a global fallback."""
    if registry_key and config_key:
        sanitized_key = re.sub(r"[^A-Za-z0-9]+", "_", config_key).strip("_").upper()
        scoped_value = os.environ.get(
            f"{registry_key.upper()}_{sanitized_key}_{env_var}"
        )
        if scoped_value:
            return scoped_value
    value = os.environ.get(env_var)
    return value if value else None


def load_config() -> "AppConfig":
    """Load, validate, resolve, and enrich the project's configuration file."""
    from pathlib import Path

    config_path = Path("podcaster.yaml")
    data = {}
    if config_path.exists():
        with config_path.open() as file_handle:
            data = yaml.safe_load(file_handle) or {}
    return resolve_refs(AppConfig.model_validate(data))
