# Proposal: Redesign Module Structure

**Date**: 2026-02-21
**Author**: Claude Agent
**Status**: Proposed

## Background

The current `aws/` directory is organized by AWS service (`s3.py`, `cloudfront.py`, `lambda_bridge.py`, `agentcore.py`), while the deploy/destroy orchestrators act as monolithic glue layers that handle naming, state, sequencing, and cross-resource wiring all in one place. This makes it hard to reason about resource groups as units, forces deploy and destroy to stay manually in sync, and scatters related resources (e.g., a Lambda function and its IAM role) across modules.

## Current Behavior

### Module organization (by AWS service)

```
aws/
├── session.py          # boto3 session
├── s3.py              # S3 bucket ops
├── cloudfront.py      # Distribution + OAC
├── lambda_bridge.py   # Lambda function + role + edge function + edge role
├── cf_function.py     # (unused)
└── agentcore.py       # Runtime + endpoint + IAM role + packaging
```

### Orchestrator pattern (deploy.py, ~237 lines)

`deploy.py` is a single function that:
1. Computes all resource names from config
2. Loads/creates state
3. Calls individual AWS functions in sequence
4. Saves state after each step
5. Manages progress UI

`destroy.py` mirrors this in reverse, but with its own resource-key lookups, its own try/except pattern per resource, and its own progress UI. Adding a new resource type requires changes in at least 3 files (aws module, deploy, destroy) plus state key coordination.

### Key problems

1. **No logical resource grouping** — Lambda function + its IAM role + its function URL are "one thing" but scattered across orchestrator logic
2. **Monolithic orchestrator** — `deploy.py` mixes naming, state, UI, creation, and cross-resource wiring in one 230-line function
3. **Manual deploy/destroy symmetry** — the destroy order must be manually kept as the reverse of deploy
4. **Ad-hoc state keys** — each resource stores state under arbitrarily-named dict keys; no structure guarantees
5. **Dead code** — `cf_function.py` is unused but still present

## Proposal

Reorganize into **resource modules** grouped by logical concern. Each module owns a cohesive resource group and exposes a uniform interface for deploy, destroy, and status. The orchestrators become thin loops over an ordered list of modules.

### New directory structure

```
src/three_stars/
├── cli.py              # CLI entry point (unchanged)
├── config.py           # Config loading (unchanged)
├── state.py            # State file I/O (unchanged)
├── deploy.py           # Thin orchestrator: loop over resources
├── destroy.py          # Thin orchestrator: loop in reverse
├── status.py           # Thin orchestrator: query each resource
├── init.py             # Project scaffolding (unchanged)
├── naming.py           # Resource naming logic (extracted from deploy.py)
└── resources/          # Resource modules (replaces aws/)
    ├── __init__.py     # RESOURCE_ORDER list
    ├── _base.py        # Common helpers (session, retry, etc.)
    ├── agentcore.py    # IAM role + runtime + endpoint + packaging
    ├── storage.py      # S3 bucket + frontend upload
    ├── api_bridge.py   # Lambda function + IAM role + function URL
    ├── edge.py         # Lambda@Edge function + IAM role (us-east-1)
    └── cdn.py          # CloudFront distribution + OACs + bucket policy
```

### Resource module interface

Each module exposes three plain functions. No base class, no ABC — just a naming convention:

```python
# resources/storage.py

def deploy(session, state, config, names, progress) -> None:
    """Create or update this resource group. Mutates state dict."""

def destroy(session, state, progress) -> None:
    """Delete this resource group. Best-effort, logs warnings."""

def status(session, state) -> list[dict]:
    """Return status rows for this resource group."""
    # Each row: {"resource": str, "id": str, "status": str}
```

**Arguments:**
- `session`: boto3.Session (created once by orchestrator)
- `state`: The full state dict (resource module reads/writes its own keys)
- `config`: ProjectConfig (read-only, only needed for deploy)
- `names`: ResourceNames dataclass (from `naming.py`)
- `progress`: Rich Progress instance (for UI updates)

### Resource ordering

`resources/__init__.py` defines the deploy order. Destroy is the reverse.

```python
# resources/__init__.py
from three_stars.resources import agentcore, api_bridge, cdn, edge, storage

# Deploy order (destroy = reversed)
RESOURCE_ORDER = [
    agentcore,   # IAM role + runtime + endpoint
    storage,     # S3 bucket + frontend files
    api_bridge,  # Lambda bridge + IAM role
    edge,        # Lambda@Edge + IAM role
    cdn,         # CloudFront distribution + OACs + bucket policy + CF→Lambda permission
]
```

### Naming module

Extract all resource name computation from `deploy.py` into a dataclass:

```python
# naming.py
from dataclasses import dataclass

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

def compute_names(config: ProjectConfig, account_id: str) -> ResourceNames:
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
    )
```

### Simplified orchestrators

**deploy.py** becomes:

```python
def run_deploy(config, profile=None):
    sess = session.create_session(region=config.region, profile=profile)
    account_id = session.get_account_id(sess)
    names = naming.compute_names(config, account_id)
    state = load_state(config.project_dir) or create_initial_state(config.name, config.region)

    with Progress(...) as progress:
        for resource in RESOURCE_ORDER:
            resource.deploy(sess, state, config, names, progress)
            save_state(config.project_dir, state)

    return {
        "cloudfront_domain": state["resources"].get("cloudfront_domain", ""),
        ...
    }
```

**destroy.py** becomes:

```python
def run_destroy(project_dir, profile=None, skip_confirm=False):
    state = load_state(project_dir)
    # ... confirmation prompt ...
    sess = session.create_session(...)

    with Progress(...) as progress:
        for resource in reversed(RESOURCE_ORDER):
            resource.destroy(sess, state, progress)

    delete_state(project_dir)
```

**status.py** becomes:

```python
def run_status(project_dir, profile=None):
    state = load_state(project_dir)
    # ... header output ...
    sess = session.create_session(...)

    table = Table(title="Resource Status")
    table.add_column("Resource", ...)
    table.add_column("ID / Name")
    table.add_column("Status")

    for resource in RESOURCE_ORDER:
        for row in resource.status(sess, state):
            table.add_row(row["resource"], row["id"], row["status"])

    console.print(table)
```

### What stays the same

- **CLI layer** (`cli.py`) — no changes
- **Config** (`config.py`) — no changes
- **State file format** — same JSON schema, same keys (backward compatible)
- **boto3 call logic** — the actual AWS API calls move file-to-file but don't change
- **Test mocking approach** — moto/mock patterns remain the same

### What gets removed

- `aws/cf_function.py` — unused dead code, delete it
- `aws/__init__.py` — replaced by `resources/__init__.py`
- The `aws/` directory entirely — replaced by `resources/`

## Impact

- **Requirements**: No change (same CLI behavior, same resource set)
- **Design**: Update `design.md` Section 4 (Component Design) to reflect new module structure. Update Section 1.1 architecture diagram to replace `aws/` with `resources/`.
- **Tasks**: New sprint with tasks for the migration (see Implementation Plan)

## Alternatives Considered

1. **Base class / ABC for resources**: Adds a class hierarchy for 5 modules. Rejected because plain functions with a naming convention are simpler, easier to grep, and sufficient for this project size.

2. **Keep `aws/` but add a registry layer**: Wrap existing modules with adapter functions. Rejected because it adds indirection without fixing the core problem (modules organized by AWS service, not by concern).

3. **CDK or CloudFormation**: Replace boto3 with IaC. Rejected because it changes the fundamental architecture and goes against the project's design choice of direct boto3 for speed and simplicity.

4. **Only refactor orchestrators**: Keep `aws/` modules as-is but loop over them. Rejected because the module boundaries themselves are the problem — `lambda_bridge.py` mixes the API bridge and the edge function, which are different logical resources with different lifecycles.

## Implementation Plan

1. **Create `naming.py`** — Extract name computation from `deploy.py` into `ResourceNames` dataclass
2. **Create `resources/` directory** with `__init__.py`, `_base.py`
3. **Migrate `agentcore.py`** — Move from `aws/` to `resources/`, add `deploy()`/`destroy()`/`status()` facade functions that wrap existing boto3 logic
4. **Create `resources/storage.py`** — Move S3 logic from `aws/s3.py`, add facade functions
5. **Create `resources/api_bridge.py`** — Move Lambda bridge logic from `aws/lambda_bridge.py` (non-edge parts), add facade functions
6. **Create `resources/edge.py`** — Move Lambda@Edge logic from `aws/lambda_bridge.py` (edge parts), add facade functions
7. **Create `resources/cdn.py`** — Move CloudFront logic from `aws/cloudfront.py`, add facade functions
8. **Rewrite `deploy.py`** — Replace monolithic function with loop over `RESOURCE_ORDER`
9. **Rewrite `destroy.py`** — Replace monolithic function with reversed loop
10. **Rewrite `status.py`** — Replace per-resource checks with loop
11. **Migrate tests** — Move from `tests/aws/` to `tests/resources/`, update imports
12. **Delete `aws/` directory** and `cf_function.py`
13. **Update `spec/design.md`** — Reflect new structure
14. **Run full test suite and linter** — Verify no regressions

## Testing Plan

- All existing tests continue to pass (updated imports only)
- Each resource module is independently testable with moto/mock
- Orchestrator tests verify the loop calls modules in correct order
- Backward compatibility: existing `.three-stars-state.json` files still work
- `ruff check` and `ruff format` pass with zero warnings
