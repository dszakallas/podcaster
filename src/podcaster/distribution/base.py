import asyncio
import logging
from abc import ABC, abstractmethod
from typing import List, Optional, Union

from ..config import (
    AppConfig,
    DistributionConfig,
    DistributionRef,
    Ref,
)
from ..notifier import Notifier, build_notifier
from ..utils import load_config

logger = logging.getLogger(__name__)


class Distribution(ABC):
    """Abstract base class for distribution mechanisms."""

    def __init__(self, notifiers: Optional[List[Notifier]] = None):
        self.notifiers: List[Notifier] = notifiers or []

    @abstractmethod
    async def _distribute(
        self,
        notebook_id: str,
        podcast_dir: Optional[str] = None,
        verbose: bool = False,
    ) -> dict:
        """Executes the specific distribution operation for a given notebook."""
        ...

    async def distribute(
        self,
        notebook_id: str,
        podcast_dir: Optional[str] = None,
        verbose: bool = False,
    ) -> dict:
        """Executes the distribution operation and runs attached notifiers concurrently."""
        result = await self._distribute(
            notebook_id, podcast_dir=podcast_dir, verbose=verbose
        )

        if self.notifiers:
            logger.info(
                f"Executing {len(self.notifiers)} post-distribution notifier(s)..."
            )
            notifier_tasks = [
                n.notify(
                    notebook_id,
                    dist_result=result,
                    podcast_dir=podcast_dir,
                    verbose=verbose,
                )
                for n in self.notifiers
            ]
            notifier_results = await asyncio.gather(
                *notifier_tasks, return_exceptions=True
            )
            notifier_outcomes = []
            for n_res in notifier_results:
                if isinstance(n_res, Exception):
                    logger.error(f"Notifier failed with exception: {n_res}")
                    notifier_outcomes.append({"status": "error", "error": str(n_res)})
                else:
                    notifier_outcomes.append(n_res)
            result["notifiers"] = notifier_outcomes

        return result


def build_distribution(
    dist_input: Union[str, DistributionRef, DistributionConfig],
    config: Optional[AppConfig] = None,
    visited_refs: Optional[set[str]] = None,
    name: Optional[str] = None,
) -> Distribution:
    """Constructs a concrete Distribution (e.g. RsyncDistribution)
    from a preset name, DistributionRef, or DistributionConfig.
    """
    from .rsync import RsyncDistribution

    if visited_refs is None:
        visited_refs = set()

    if config is None:
        config = load_config()

    if isinstance(dist_input, str):
        ref_name = dist_input
        if ref_name in visited_refs:
            raise ValueError(
                f"Circular reference detected in distributions: {ref_name}"
            )
        if ref_name not in config.distributions:
            raise ValueError(f"Distribution '{ref_name}' not found in configuration.")
        visited_refs.add(ref_name)
        dist_cfg = config.distributions[ref_name]
        return build_distribution(
            dist_cfg, config, visited_refs=visited_refs, name=ref_name
        )

    elif isinstance(dist_input, Ref):
        current_name = name or dist_input.ref
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
            return build_distribution(
                dist_cfg, config, visited_refs=visited_refs, name=ref_name
            )
        else:
            rsync_cfg = getattr(dist_input, "rsync", None)
            notifiers = getattr(dist_input, "notifiers", []) or []
            dist_cfg = DistributionConfig(rsync=rsync_cfg, notifiers=notifiers)
            return build_distribution(
                dist_cfg, config, visited_refs=visited_refs, name=current_name
            )

    elif isinstance(dist_input, DistributionConfig):
        dist_cfg = dist_input

    else:
        raise ValueError(f"Invalid distribution input type: {type(dist_input)}")

    built_notifiers = [
        build_notifier(n_ref, config=config) for n_ref in dist_cfg.notifiers
    ]

    if dist_cfg.rsync is not None:
        dest = dist_cfg.rsync.destination
        method = dist_cfg.rsync.method or "rsync"
        flags = dist_cfg.rsync.flags
        return RsyncDistribution(
            destination=dest or "",
            method=method,
            flags=flags,
            notifiers=built_notifiers,
            name=name,
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
