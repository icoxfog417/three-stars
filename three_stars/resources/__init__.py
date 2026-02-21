"""Resource modules for three-stars.

Each module owns a cohesive resource group and exposes deploy(), destroy(),
and get_status() functions. Modules never import each other — all
cross-resource wiring lives in the orchestrator.
"""

from __future__ import annotations

from typing import NamedTuple


class ResourceStatus(NamedTuple):
    """A single row in the status table."""

    resource: str
    id: str
    status: str
