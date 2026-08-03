import logging
import os
import subprocess
from typing import Optional

from ..notifier import Notifier
from ..utils import find_notebook_dir, load_config, setup_logging
from .base import Distribution

logger = logging.getLogger(__name__)


def rsync_dir(src: str, dst: str, flags: Optional[list[str]] = None):
    """Rsyncs a directory to a destination, creating parent if needed."""
    os.makedirs(os.path.dirname(dst.rstrip("/")), exist_ok=True)
    cmd = [
        "rsync",
        "-avz",
        "--include=*/",
        "--include=*.m4a",
        "--include=*.lrc",
        "--exclude=*",
    ]
    if flags:
        cmd.extend(flags)
    cmd.extend(
        [
            src.rstrip("/") + "/",
            dst.rstrip("/") + "/",
        ]
    )
    subprocess.run(cmd, check=True)


def rclone_copy_dir(src: str, dst: str, flags: Optional[list[str]] = None):
    """Copies a directory using rclone, creating parent if needed."""
    cmd = ["rclone", "copy"]
    if flags:
        cmd.extend(flags)
    cmd.extend(["--include", "*.m4a", "--include", "*.lrc", src, dst])
    subprocess.run(cmd, check=True)


async def sync_podcast(
    notebook_id: str,
    destination: str,
    method: str = "rsync",
    podcast_dir: Optional[str] = None,
    verbose: bool = False,
    flags: Optional[list[str]] = None,
) -> dict:
    if verbose:
        setup_logging(verbose)

    config = load_config()
    if not podcast_dir:
        podcast_dir = config.podcast_dir

    notebook_dir_name = find_notebook_dir(podcast_dir, notebook_id)
    if not notebook_dir_name:
        raise FileNotFoundError(
            f"Notebook directory not found for notebook ID: {notebook_id}"
        )

    source_dir = os.path.join(podcast_dir, notebook_dir_name)

    if not os.path.exists(source_dir):
        raise FileNotFoundError(f"Source directory {source_dir} not found.")

    dst_path = os.path.join(destination, notebook_dir_name)

    if method == "rsync":
        logger.info(f"Rsyncing {source_dir} to {dst_path}...")
        rsync_dir(source_dir, dst_path, flags=flags)
    elif method == "rclone":
        logger.info(f"Rclone copying {source_dir} to {dst_path}...")
        rclone_copy_dir(source_dir, dst_path, flags=flags)
    else:
        raise ValueError(f"Unknown sync method: {method}")

    return {
        "notebook_id": notebook_id,
        "source": source_dir,
        "destination": dst_path,
        "method": method,
        "status": "success",
    }


class RsyncDistribution(Distribution):
    """Rsync/Rclone distribution mechanism implementation."""

    def __init__(
        self,
        destination: str,
        method: str = "rsync",
        flags: Optional[list[str]] = None,
        notifiers: Optional[list[Notifier]] = None,
        name: Optional[str] = None,
    ):
        super().__init__(notifiers=notifiers)
        self.destination = destination
        self.method = method
        self.flags = flags or []
        self.name = name

    async def _distribute(
        self,
        notebook_id: str,
        podcast_dir: Optional[str] = None,
        verbose: bool = False,
    ) -> dict:
        res = await sync_podcast(
            notebook_id,
            self.destination,
            method=self.method,
            podcast_dir=podcast_dir,
            verbose=verbose,
            flags=self.flags,
        )
        if self.name:
            res["distribution"] = self.name
        return res
