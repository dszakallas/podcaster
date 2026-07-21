import logging
from abc import ABC, abstractmethod
from typing import Optional, Union

from ..config import (
    AppConfig,
    DistributionConfig,
    DistributionRef,
    Ref,
)
from ..utils import load_config

logger = logging.getLogger(__name__)


class Distribution(ABC):
    """Abstract base class for distribution mechanisms."""

    @abstractmethod
    async def distribute(
        self,
        notebook_id: str,
        podcast_dir: Optional[str] = None,
        verbose: bool = False,
    ) -> dict:
        """Executes the distribution operation for a given notebook."""
        ...


def build_distribution(
    dist_input: Union[str, DistributionRef, DistributionConfig],
    config: Optional[AppConfig] = None,
    visited_refs: Optional[set[str]] = None,
) -> Distribution:
    """Constructs a concrete Distribution (RsyncDistribution or PlexDistribution)
    from a preset name, DistributionRef, or DistributionConfig.
    """
    from .plex import PlexDistribution
    from .rsync import RsyncDistribution

    if visited_refs is None:
        visited_refs = set()

    if config is None:
        config = load_config()

    if isinstance(dist_input, str):
        name = dist_input
        if name in visited_refs:
            raise ValueError(f"Circular reference detected in distributions: {name}")
        if name not in config.distributions:
            raise ValueError(f"Distribution '{name}' not found in configuration.")
        visited_refs.add(name)
        dist_cfg = config.distributions[name]
        override_rsync = None
        override_plex = None
    elif isinstance(dist_input, DistributionRef):
        if dist_input.ref:
            ref_name = dist_input.ref
            if ref_name in visited_refs:
                raise ValueError(
                    f"Circular reference detected in distributions: {ref_name}"
                )
            if ref_name not in config.distributions:
                raise ValueError(
                    f"Distribution '{ref_name}' not found in configuration."
                )
            visited_refs.add(ref_name)
            dist_cfg = config.distributions[ref_name]
        else:
            dist_cfg = None
        override_rsync = dist_input.rsync
        override_plex = dist_input.plex
    elif isinstance(dist_input, DistributionConfig):
        dist_cfg = dist_input
        override_rsync = None
        override_plex = None
    else:
        raise ValueError(f"Invalid distribution input type: {type(dist_input)}")

    rsync_cfg = override_rsync or (dist_cfg.rsync if dist_cfg else None)
    plex_cfg = override_plex or (dist_cfg.plex if dist_cfg else None)

    if rsync_cfg is not None:
        if isinstance(rsync_cfg, str):
            return build_distribution(rsync_cfg, config, visited_refs=visited_refs)
        if isinstance(rsync_cfg, Ref) and rsync_cfg.ref and not rsync_cfg.destination:
            return build_distribution(rsync_cfg.ref, config, visited_refs=visited_refs)

        dest = rsync_cfg.destination
        method = rsync_cfg.method or "rsync"
        flags = rsync_cfg.flags
        return RsyncDistribution(
            destination=dest or "",
            method=method,
            flags=flags,
        )
    elif plex_cfg is not None:
        if isinstance(plex_cfg, str):
            return build_distribution(plex_cfg, config, visited_refs=visited_refs)
        if isinstance(plex_cfg, Ref) and plex_cfg.ref and plex_cfg.section_id is None:
            return build_distribution(plex_cfg.ref, config, visited_refs=visited_refs)

        return PlexDistribution(
            section_id=plex_cfg.section_id or 0,
            server_library_path=plex_cfg.server_library_path,
            server_url=plex_cfg.server_url,
            token=plex_cfg.token,
            rsync_config=plex_cfg.rsync,
            config=config,
        )
    else:
        raise ValueError(
            f"Could not construct Distribution from configuration: {dist_input}"
        )


async def execute_distribution(
    dist_input: Union[str, DistributionRef, DistributionConfig],
    notebook_id: str,
    podcast_dir: Optional[str] = None,
    verbose: bool = False,
) -> dict:
    """Executes a distribution for a given notebook."""
    config = load_config()
    dist_obj = build_distribution(dist_input, config)
    return await dist_obj.distribute(
        notebook_id, podcast_dir=podcast_dir, verbose=verbose
    )
