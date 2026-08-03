import logging
from abc import ABC, abstractmethod
from typing import Optional, Union

from ..config import (
    AppConfig,
    NotifierConfig,
    NotifierRef,
    Ref,
)
from ..utils import load_config

logger = logging.getLogger(__name__)


class Notifier(ABC):
    """Abstract base class for notification mechanisms."""

    @abstractmethod
    async def notify(
        self,
        notebook_id: str,
        dist_result: Optional[dict] = None,
        podcast_dir: Optional[str] = None,
        verbose: bool = False,
    ) -> dict:
        """Executes the notification operation."""
        ...


def build_notifier(
    notifier_input: Union[str, NotifierRef, NotifierConfig],
    config: Optional[AppConfig] = None,
    visited_refs: Optional[set[str]] = None,
    name: Optional[str] = None,
) -> Notifier:
    """Constructs a concrete Notifier (PlexNotifier or DiscordNotifier)
    from a preset name, NotifierRef, or NotifierConfig.
    """
    from .discord import DiscordNotifier
    from .plex import PlexNotifier

    if visited_refs is None:
        visited_refs = set()

    if config is None:
        config = load_config()

    if isinstance(notifier_input, str):
        ref_name = notifier_input
        if ref_name in visited_refs:
            raise ValueError(f"Circular reference detected in notifiers: {ref_name}")
        if ref_name not in config.notifiers:
            raise ValueError(f"Notifier '{ref_name}' not found in configuration.")
        visited_refs.add(ref_name)
        cfg = config.notifiers[ref_name]
        return build_notifier(cfg, config, visited_refs=visited_refs, name=ref_name)

    elif isinstance(notifier_input, Ref):
        current_name = name or notifier_input.ref
        if notifier_input.ref:
            ref_name = notifier_input.ref
            if ref_name in visited_refs:
                raise ValueError(
                    f"Circular reference detected in notifiers: {ref_name}"
                )
            if ref_name not in config.notifiers:
                raise ValueError(f"Notifier '{ref_name}' not found in configuration.")
            visited_refs.add(ref_name)
            cfg = config.notifiers[ref_name]
            return build_notifier(cfg, config, visited_refs=visited_refs, name=ref_name)
        else:
            plex_cfg = getattr(notifier_input, "plex", None)
            discord_cfg = getattr(notifier_input, "discord", None)
            cfg = NotifierConfig(plex=plex_cfg, discord=discord_cfg)
            return build_notifier(
                cfg, config, visited_refs=visited_refs, name=current_name
            )

    elif isinstance(notifier_input, NotifierConfig):
        if notifier_input.plex is not None:
            return PlexNotifier(
                section_id=notifier_input.plex.section_id,
                server_library_path=notifier_input.plex.server_library_path,
                server_url=notifier_input.plex.server_url,
                token=notifier_input.plex.token,
                name=name,
            )
        elif notifier_input.discord is not None:
            return DiscordNotifier(
                webhook_url=notifier_input.discord.webhook_url,
                bot_token=notifier_input.discord.bot_token,
                channel_id=notifier_input.discord.channel_id,
                name=name,
            )
        else:
            raise ValueError(
                f"NotifierConfig has no notifier specified: {notifier_input}"
            )

    else:
        raise ValueError(f"Invalid notifier input type: {type(notifier_input)}")
