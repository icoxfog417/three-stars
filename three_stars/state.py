"""Deployment state management."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

STATE_FILENAME = ".three-stars-state.json"
STATE_VERSION = 1


def get_state_path(project_dir: str | Path) -> Path:
    """Get the path to the state file."""
    return Path(project_dir).resolve() / STATE_FILENAME


def load_state(project_dir: str | Path) -> dict | None:
    """Load deployment state from file.

    Returns None if no state file exists.
    """
    state_path = get_state_path(project_dir)
    if not state_path.exists():
        return None

    with open(state_path) as f:
        return json.load(f)


def save_state(project_dir: str | Path, state: dict) -> None:
    """Save deployment state to file."""
    state_path = get_state_path(project_dir)
    state["version"] = STATE_VERSION
    state["updated_at"] = datetime.now(UTC).isoformat()

    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)


def backup_state(project_dir: str | Path) -> Path | None:
    """Backup the state file before a new deployment.

    Copies .three-stars-state.json to .three-stars-state.json.bak.
    Returns the backup path, or None if no state file exists.
    """
    state_path = get_state_path(project_dir)
    if not state_path.exists():
        return None
    backup_path = state_path.with_suffix(".json.bak")
    import shutil

    shutil.copy2(state_path, backup_path)
    return backup_path


def delete_state(project_dir: str | Path) -> None:
    """Delete the state file."""
    state_path = get_state_path(project_dir)
    if state_path.exists():
        state_path.unlink()


def create_initial_state(project_name: str, region: str) -> dict:
    """Create an initial state dict."""
    return {
        "version": STATE_VERSION,
        "project_name": project_name,
        "region": region,
        "deployed_at": datetime.now(UTC).isoformat(),
        "resources": {},
    }
