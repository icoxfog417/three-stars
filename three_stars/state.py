"""Deployment state persistence with typed dataclasses."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

STATE_FILENAME = ".three-stars-state.json"
STATE_VERSION = 1


@dataclass
class AgentCoreState:
    iam_role_name: str
    iam_role_arn: str
    runtime_id: str
    runtime_arn: str
    endpoint_name: str
    endpoint_arn: str


@dataclass
class StorageState:
    s3_bucket: str


@dataclass
class EdgeState:
    role_name: str
    role_arn: str
    function_name: str
    function_arn: str


@dataclass
class CdnState:
    distribution_id: str
    domain: str
    arn: str
    oac_id: str
    lambda_oac_id: str


@dataclass
class DeploymentState:
    version: int
    project_name: str
    region: str
    deployed_at: str
    updated_at: str | None = None
    agentcore: AgentCoreState | None = None
    storage: StorageState | None = None
    edge: EdgeState | None = None
    cdn: CdnState | None = None


_RESOURCE_STATE_CLASSES = {
    "agentcore": AgentCoreState,
    "storage": StorageState,
    "edge": EdgeState,
    "cdn": CdnState,
}


def get_state_path(project_dir: str | Path) -> Path:
    """Get the path to the state file."""
    return Path(project_dir).resolve() / STATE_FILENAME


def load_state(project_dir: str | Path) -> DeploymentState | None:
    """Load deployment state from file.

    Returns None if no state file exists.
    """
    state_path = get_state_path(project_dir)
    if not state_path.exists():
        return None

    with open(state_path) as f:
        data = json.load(f)

    kwargs: dict = {
        "version": data["version"],
        "project_name": data["project_name"],
        "region": data["region"],
        "deployed_at": data["deployed_at"],
        "updated_at": data.get("updated_at"),
    }
    for field_name, cls in _RESOURCE_STATE_CLASSES.items():
        raw = data.get(field_name)
        if raw is not None:
            kwargs[field_name] = cls(**raw)

    return DeploymentState(**kwargs)


def save_state(project_dir: str | Path, state: DeploymentState) -> None:
    """Save deployment state to file."""
    state_path = get_state_path(project_dir)
    state.updated_at = datetime.now(UTC).isoformat()

    with open(state_path, "w") as f:
        json.dump(asdict(state), f, indent=2)


def backup_state(project_dir: str | Path) -> Path | None:
    """Backup the state file before a new deployment.

    Copies .three-stars-state.json to .three-stars-state.json.bak.
    Returns the backup path, or None if no state file exists.
    """
    state_path = get_state_path(project_dir)
    if not state_path.exists():
        return None
    backup_path = state_path.with_suffix(".json.bak")
    shutil.copy2(state_path, backup_path)
    return backup_path


def delete_state(project_dir: str | Path) -> None:
    """Delete the state file."""
    state_path = get_state_path(project_dir)
    if state_path.exists():
        state_path.unlink()


def create_initial_state(project_name: str, region: str) -> DeploymentState:
    """Create an initial deployment state."""
    return DeploymentState(
        version=STATE_VERSION,
        project_name=project_name,
        region=region,
        deployed_at=datetime.now(UTC).isoformat(),
    )
