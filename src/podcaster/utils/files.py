"""Filesystem and filename helpers."""

from pathlib import Path


def sanitize(value: str) -> str:
    """Sanitize a string for use in a filename."""
    return "".join(character if character.isalnum() else "_" for character in value)


def get_workflow_dir(workdir: str | Path, workflow_id: str) -> Path:
    """Get or create the directory for a workflow run."""
    workflow_dir = Path(workdir) / workflow_id
    workflow_dir.mkdir(parents=True, exist_ok=True)
    return workflow_dir
