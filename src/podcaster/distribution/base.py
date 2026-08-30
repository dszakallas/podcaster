import asyncio
import logging
from abc import ABC, abstractmethod
from typing import List, Optional

from ..config import (
    DistributionConfig,
    NotifierConfig,
)
from ..notifier import Notifier, build_notifier

logger = logging.getLogger(__name__)


class Distribution(ABC):
    """Abstract base class for distribution mechanisms."""

    name: Optional[str]

    def __init__(self, notifiers: Optional[List[Notifier]] = None):
        self.notifiers: List[Notifier] = notifiers or []

    @abstractmethod
    async def _distribute(
        self,
        working_dir: str,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Executes the specific distribution operation for a given working directory."""
        ...

    async def distribute(
        self,
        working_dir: str,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Executes the distribution operation and runs attached notifiers concurrently."""
        result = await self._distribute(working_dir=working_dir, metadata=metadata)

        if self.notifiers:
            logger.info(
                f"Executing {len(self.notifiers)} post-distribution notifier(s)..."
            )
            notifier_tasks = [
                n.notify(
                    metadata=metadata,
                    dist_result=result,
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
    dist_cfg: DistributionConfig,
    name: Optional[str] = None,
) -> Distribution:
    """Constructs a concrete Distribution (e.g. RsyncDistribution)
    from a resolved DistributionConfig.
    """
    from .rsync import RsyncDistribution

    name = name or getattr(dist_cfg, "_ref_name", None)
    built_notifiers = [
        build_notifier(n) for n in dist_cfg.notifiers if isinstance(n, NotifierConfig)
    ]

    if dist_cfg.rsync is not None:
        return RsyncDistribution(
            destination=dist_cfg.rsync.destination or "",
            method=dist_cfg.rsync.method or "rsync",
            flags=dist_cfg.rsync.flags,
            filename_template=dist_cfg.rsync.filename_template,
            notifiers=built_notifiers,
            name=name,
        )
    else:
        raise ValueError(
            f"Could not construct Distribution from configuration: {dist_cfg}"
        )


async def execute_distribution(
    dist_input: DistributionConfig,
    working_dir: str,
    metadata: Optional[dict] = None,
) -> dict:
    """Executes a distribution for a given working directory."""
    dist_obj = build_distribution(dist_input)
    return await dist_obj.distribute(working_dir=working_dir, metadata=metadata)
