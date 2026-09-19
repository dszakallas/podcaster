import logging
from abc import ABC, abstractmethod
from collections.abc import Callable

from ..config import NotifierConfig

logger = logging.getLogger(__name__)


class Notifier(ABC):
    """Abstract base class for notification mechanisms."""

    name: str | None

    @abstractmethod
    async def notify(
        self,
        metadata: dict | None = None,
        dist_result: dict | None = None,
    ) -> dict:
        """Executes the notification operation."""
        ...


NotifierFactory = Callable[[NotifierConfig, str | None], Notifier]
_NOTIFIER_REGISTRY: dict[str, NotifierFactory] = {}


def register_notifier(key: str, factory: NotifierFactory) -> None:
    """Register a notifier factory for a NotifierConfig field name."""
    _NOTIFIER_REGISTRY[key] = factory


def _ensure_default_notifiers_registered() -> None:
    if not _NOTIFIER_REGISTRY:
        from . import discord, plex  # noqa: F401


def build_notifier(
    notifier_cfg: NotifierConfig,
    name: str | None = None,
) -> Notifier:
    """Constructs a concrete Notifier from a resolved NotifierConfig."""
    _ensure_default_notifiers_registered()
    name = name or getattr(notifier_cfg, "_ref_name", None)

    for key, factory in _NOTIFIER_REGISTRY.items():
        if getattr(notifier_cfg, key, None) is not None:
            return factory(notifier_cfg, name)

    raise ValueError(f"NotifierConfig has no notifier specified: {notifier_cfg}")
