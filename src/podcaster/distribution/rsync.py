import asyncio
import logging
import os
import subprocess
from typing import Optional

from ..notifier import Notifier
from .base import Distribution

logger = logging.getLogger(__name__)


def rsync_dir(src: str, dst: str, flags: Optional[list[str]] = None):
    """Rsyncs a directory to a destination, creating parent if needed."""
    parent_dir = os.path.dirname(dst.rstrip("/"))
    if parent_dir and ":" not in parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
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
    working_dir: str,
    destination: str,
    method: str = "rsync",
    flags: Optional[list[str]] = None,
) -> dict:

    if not os.path.exists(working_dir):
        raise FileNotFoundError(f"Source directory {working_dir} not found.")

    folder_name = os.path.basename(os.path.abspath(working_dir))
    dst_path = os.path.join(destination, folder_name)

    if method == "rsync":
        logger.info(f"Rsyncing {working_dir} to {dst_path}...")
        await asyncio.to_thread(rsync_dir, working_dir, dst_path, flags)
    elif method == "rclone":
        logger.info(f"Rclone copying {working_dir} to {dst_path}...")
        await asyncio.to_thread(rclone_copy_dir, working_dir, dst_path, flags)
    else:
        raise ValueError(f"Unknown sync method: {method}")

    return {
        "source": working_dir,
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
        working_dir: str,
    ) -> dict:
        res = await sync_podcast(
            working_dir=working_dir,
            destination=self.destination,
            method=self.method,
            flags=self.flags,
        )
        if self.name:
            res["distribution"] = self.name
        return res
