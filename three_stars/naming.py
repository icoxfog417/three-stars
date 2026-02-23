"""Resource naming conventions for three-stars."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from three_stars.config import ProjectConfig


@dataclass(frozen=True)
class ResourceNames:
    prefix: str
    bucket: str
    agentcore_role: str
    agent_name: str
    endpoint_name: str
    lambda_role: str
    lambda_function: str
    edge_role: str
    edge_function: str
    memory: str


def get_resource_prefix(config: ProjectConfig) -> str:
    """Generate a prefix for AWS resource names."""
    return f"sss-{config.name}"


def compute_names(config: ProjectConfig, account_id: str) -> ResourceNames:
    """Compute all AWS resource names from config and account ID."""
    prefix = get_resource_prefix(config)
    ac_prefix = prefix.replace("-", "_")
    return ResourceNames(
        prefix=prefix,
        bucket=f"{prefix}-{_short_hash(account_id)}",
        agentcore_role=f"{prefix}-role",
        agent_name=f"{ac_prefix}_agent",
        endpoint_name=f"{ac_prefix}_endpoint",
        lambda_role=f"{prefix}-lambda-role",
        lambda_function=f"{prefix}-api-bridge",
        edge_role=f"{prefix}-edge-role",
        edge_function=f"{prefix}-edge-sha256",
        memory=f"{ac_prefix}_memory",
    )


def _short_hash(value: str) -> str:
    """Generate a short hash for resource name uniqueness."""
    return hashlib.sha256(value.encode()).hexdigest()[:8]
