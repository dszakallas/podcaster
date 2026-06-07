import os
import sys
import logging
import yaml
from pathlib import Path
from datetime import datetime
from typing import Optional

def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stderr)]
    )

def load_config():
    config_path = Path("podcaster.yaml")
    if config_path.exists():
        with open(config_path, "r") as f:
            return yaml.safe_load(f)
    return {}

def get_storage_path() -> str:
    """Get the NotebookLM storage state path."""
    storage_path = os.environ.get("NOTEBOOKLM_STORAGE_STATE")
    if not storage_path:
        home = os.environ.get("NOTEBOOKLM_HOME", "~/.notebooklm")
        storage_path = os.path.expanduser(os.path.join(home, "storage_state.json"))
    return storage_path

def sanitize(s: str) -> str:
    """Sanitize string for filenames."""
    return "".join([c if c.isalnum() else "_" for c in s])

def get_notebook_dir_name(title: str, notebook_id: str, created_at: Optional[datetime] = None) -> str:
    """Get the standardized directory name for a notebook with ID suffix."""
    safe_title = sanitize(title)
    if created_at:
        date_str = created_at.strftime("%Y-%m-%d")
        name = f"{date_str} - {safe_title}"
    else:
        name = safe_title
    return f"{name} [nlm_{notebook_id}]"

def find_notebook_dir(base_dir: str, notebook_id: str) -> Optional[str]:
    """Find an existing notebook directory by matching the [nlm_id] suffix."""
    if not os.path.exists(base_dir):
        return None
        
    suffix = f"[nlm_{notebook_id}]"
    for entry in os.listdir(base_dir):
        full_path = os.path.join(base_dir, entry)
        if os.path.isdir(full_path) and entry.endswith(suffix):
            return entry
    return None

def get_or_create_notebook_dir(base_dir: str, notebook_id: str, title: str, created_at: Optional[datetime] = None) -> str:
    """Find or create a standardized notebook directory."""
    notebook_dir_name = find_notebook_dir(base_dir, notebook_id)
    if not notebook_dir_name:
        notebook_dir_name = get_notebook_dir_name(title, notebook_id, created_at)
    
    notebook_dir = os.path.join(base_dir, notebook_dir_name)
    os.makedirs(notebook_dir, exist_ok=True)
    return notebook_dir
