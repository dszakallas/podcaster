import asyncio
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from jinja2 import Environment, StrictUndefined

from ..notifier import Notifier
from ..utils.files import sanitize
from .base import Distribution

logger = logging.getLogger(__name__)

_TEMPLATE_ENV = Environment(undefined=StrictUndefined, autoescape=False)


def _render_relative_path(template: str, metadata: dict, artifact: dict) -> Path:
    """Render and validate a relative destination path for one artifact."""
    notebook = metadata["notebook"]
    rendered = _TEMPLATE_ENV.from_string(template).render(
        id=metadata.get("id"), notebook=notebook, artifact=artifact
    )
    path = Path(rendered)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("filename_template must produce a relative path")
    return path


def _link_artifact_files(
    working_dir: str,
    staging_dir: str,
    filename_template: str,
    metadata: dict,
) -> None:
    """Create a temporary, template-shaped tree from podcast artifact metadata."""
    notebook_metadata = metadata.get("notebook")
    artifacts = metadata.get("artifacts")
    if not isinstance(notebook_metadata, dict) or not isinstance(artifacts, list):
        raise ValueError("Distribution requires notebook and artifacts metadata")
    notebook = {
        **notebook_metadata,
        "title": sanitize(str(notebook_metadata.get("title", ""))),
    }
    template_metadata = {**metadata, "notebook": notebook}

    for item in artifacts:
        if not isinstance(item, dict):
            raise ValueError("Distribution artifact metadata must be an object")
        source_paths = [item.get("path"), item.get("lrc_path")]
        artifact = {
            "id": item.get("id", ""),
            "name": sanitize(str(item.get("name", ""))),
        }
        relative_base = _render_relative_path(
            filename_template, template_metadata, artifact
        )
        for source in source_paths:
            if not source or not os.path.exists(source):
                continue
            suffix = Path(source).suffix
            if suffix not in {".m4a", ".lrc"}:
                continue
            destination = Path(staging_dir) / relative_base.with_suffix(suffix)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.link(source, destination)


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
    filename_template: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:

    if not os.path.exists(working_dir):
        raise FileNotFoundError(f"Source directory {working_dir} not found.")

    folder_name = os.path.basename(os.path.abspath(working_dir))
    dst_path = os.path.join(destination, folder_name)

    if filename_template and metadata:
        with tempfile.TemporaryDirectory(dir=working_dir) as staging_dir:
            _link_artifact_files(working_dir, staging_dir, filename_template, metadata)
            if method == "rsync":
                logger.info("Rsyncing %s to %s...", working_dir, destination)
                await asyncio.to_thread(rsync_dir, staging_dir, destination, flags)
            elif method == "rclone":
                logger.info("Rclone copying %s to %s...", working_dir, destination)
                await asyncio.to_thread(
                    rclone_copy_dir, staging_dir, destination, flags
                )
            else:
                raise ValueError(f"Unknown sync method: {method}")
        return {
            "source": working_dir,
            "destination": destination,
            "method": method,
            "status": "success",
        }

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
        filename_template: Optional[str] = None,
        notifiers: Optional[list[Notifier]] = None,
        name: Optional[str] = None,
    ):
        super().__init__(notifiers=notifiers)
        self.destination = destination
        self.method = method
        self.flags = flags or []
        self.filename_template = filename_template
        self.name = name

    async def _distribute(
        self,
        working_dir: str,
        metadata: Optional[dict] = None,
    ) -> dict:
        res = await sync_podcast(
            working_dir=working_dir,
            destination=self.destination,
            method=self.method,
            flags=self.flags,
            filename_template=self.filename_template,
            metadata=metadata,
        )
        if self.name:
            res["distribution"] = self.name
        return res
