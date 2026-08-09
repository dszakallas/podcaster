import logging
from abc import ABC, abstractmethod
from typing import Optional

from ..config import NotifierConfig

logger = logging.getLogger(__name__)


class Notifier(ABC):
    """Abstract base class for notification mechanisms."""

    @abstractmethod
    async def notify(
        self,
        notebook_id: str,
        dist_result: Optional[dict] = None,
        podcast_dir: Optional[str] = None,
    ) -> dict:
        """Executes the notification operation."""
        ...


def build_notifier(
    notifier_cfg: NotifierConfig,
    name: Optional[str] = None,
) -> Notifier:
    """Constructs a concrete Notifier (PlexNotifier or DiscordNotifier)
    from a resolved NotifierConfig.
    """
    from ..utils import load_config
    from .discord import DiscordNotifier
    from .plex import PlexNotifier

    if not name:
        try:
            app_cfg = load_config()
            name = next(
                (k for k, v in app_cfg.notifiers.items() if v is notifier_cfg),
                None,
            )
        except Exception:
            pass

    if notifier_cfg.plex is not None:
        return PlexNotifier(
            section_id=notifier_cfg.plex.section_id,
            server_library_path=notifier_cfg.plex.server_library_path,
            server_url=notifier_cfg.plex.server_url,
            token=notifier_cfg.plex.token,
            name=name,
        )
    elif notifier_cfg.discord is not None:
        return DiscordNotifier(
            webhook_url=notifier_cfg.discord.webhook_url,
            bot_token=notifier_cfg.discord.bot_token,
            channel_id=notifier_cfg.discord.channel_id,
            name=name,
        )
    else:
        raise ValueError(f"NotifierConfig has no notifier specified: {notifier_cfg}")
