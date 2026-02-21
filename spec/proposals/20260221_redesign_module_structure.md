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

## Design Philosophy: Orchestrator as Explicit Dependency Manager

Our fundamental advantage over CDK is the **explicit orchestrator**. CDK uses a declarative dependency graph that can produce frustrating circular dependency errors and opaque resolution order. Our approach eliminates this entirely:

- **Resources are independent units** — each module knows how to create/destroy its own resources, but knows nothing about other resources
- **The orchestrator explicitly threads data** — outputs from one resource (ARNs, URLs, IDs) are passed as inputs to the next by the orchestrator
- **Order is guaranteed acyclic** — it's a flat list, not a graph. No cycles possible.
- **Dependencies are visible** — reading the orchestrator shows exactly what flows from where to where

This is the key design constraint: **resource modules never import or reference each other**. All cross-resource wiring lives in the orchestrator.

## Proposal

Reorganize into **resource modules** grouped by logical concern. Each module owns a cohesive resource group and exposes a uniform interface. The orchestrator manages deployment order and explicitly passes cross-resource outputs as inputs.

### New directory structure

```
src/three_stars/
├── cli.py              # (unchanged)
├── config.py           # (unchanged)
├── state.py            # (unchanged)
├── deploy.py           # Orchestrator: manages order + cross-resource data flow
├── destroy.py          # Orchestrator: reverse order
├── status.py           # Orchestrator: query each resource
├── init.py             # (unchanged)
├── naming.py           # Resource naming (extracted from deploy.py)
└── resources/          # Resource modules (replaces aws/)
    ├── __init__.py     # RESOURCE_ORDER list, DeployContext/DestroyContext
    ├── _base.py        # Shared helpers (session creation, progress utils)
    ├── agentcore.py    # IAM role + runtime + endpoint + packaging
    ├── storage.py      # S3 bucket + frontend upload
    ├── api_bridge.py   # Lambda function + IAM role + function URL
    ├── edge.py         # Lambda@Edge function + IAM role (us-east-1)
    └── cdn.py          # CloudFront distribution + OACs + bucket policy
```

### Resource module interface

Each module exposes three plain functions with **explicit inputs and outputs**:

```python
# resources/storage.py  (example)

def deploy(session, config, names) -> dict:
    """Create or update this resource group.

    Returns:
        Dict of resource outputs (IDs, ARNs, names) to persist in state.
        Keys are state field names. e.g. {"s3_bucket": "sss-my-app-abc123"}
    """

def destroy(session, state) -> None:
    """Delete this resource group. Best-effort, logs warnings on failure."""

def get_status(session, state) -> list[ResourceStatus]:
    """Return status rows for this resource group."""
```

**Key design rules:**
- `deploy()` receives only what it needs: session, config, names, plus **explicit kwargs for cross-resource data**
- `deploy()` returns a dict of state entries it created — the orchestrator saves them
- `destroy()` reads from state to find its own resource IDs — no cross-resource data needed
- Modules **never import each other** — all wiring is in the orchestrator

### Cross-resource data flow (the orchestrator's job)

The deploy orchestrator explicitly passes outputs from earlier resources as inputs to later ones:

```python
# deploy.py — the orchestrator IS the dependency manager

def run_deploy(config, profile=None):
    sess = create_session(region=config.region, profile=profile)
    account_id = get_account_id(sess)
    names = compute_names(config, account_id)
    state = load_state(config.project_dir) or create_initial_state(...)

    with Progress(...) as progress:

        # 1. AgentCore: no dependencies on other resources
        ac_out = agentcore.deploy(sess, config, names)
        state["resources"].update(ac_out)
        save_state(config.project_dir, state)

        # 2. Storage: no dependencies on other resources
        st_out = storage.deploy(sess, config, names)
        state["resources"].update(st_out)
        save_state(config.project_dir, state)

        # 3. API Bridge: needs agentcore runtime ARN
        ab_out = api_bridge.deploy(sess, config, names,
            agent_runtime_arn=ac_out["agentcore_runtime_arn"],
        )
        state["resources"].update(ab_out)
        save_state(config.project_dir, state)

        # 4. Edge: no dependencies on other resources
        edge_out = edge.deploy(sess, names)
        state["resources"].update(edge_out)
        save_state(config.project_dir, state)

        # 5. CDN: needs bucket, lambda URL, edge ARN (wires everything together)
        cdn_out = cdn.deploy(sess, config, names,
            bucket_name=st_out["s3_bucket"],
            lambda_function_url=ab_out["lambda_function_url"],
            lambda_function_name=ab_out["lambda_function_name"],
            edge_function_arn=edge_out["edge_function_arn"],
        )
        state["resources"].update(cdn_out)
        save_state(config.project_dir, state)

    return { ... }
```

**Why this is better than a generic loop:**
- Cross-resource dependencies are **explicit typed arguments**, not implicit state-dict key lookups
- Reading the orchestrator shows the full data flow at a glance
- Adding a new resource = add a module + add a step in the orchestrator with clear inputs/outputs
- No risk of key-name typos causing silent failures — function signatures enforce correctness

### Destroy orchestrator

Destroy is simpler — each module only needs the state dict to find its own resource IDs:

```python
# destroy.py

def run_destroy(project_dir, profile=None, skip_confirm=False):
    state = load_state(project_dir)
    # ... confirmation prompt ...
    sess = create_session(...)

    with Progress(...) as progress:
        # Reverse order: CDN first (depends on others), AgentCore last
        cdn.destroy(sess, state, progress)
        edge.destroy(sess, state, progress)
        api_bridge.destroy(sess, state, progress)
        storage.destroy(sess, state, progress)
        agentcore.destroy(sess, state, progress)

    delete_state(project_dir)
```

### Status orchestrator

```python
# status.py

def run_status(project_dir, profile=None):
    state = load_state(project_dir)
    sess = create_session(...)

    table = Table(title="Resource Status")
    table.add_column("Resource", style="bold")
    table.add_column("ID / Name")
    table.add_column("Status")

    for resource in [agentcore, storage, api_bridge, edge, cdn]:
        for row in resource.get_status(sess, state):
            table.add_row(row.resource, row.id, row.status)

    console.print(table)
```

### Naming module

Extract all resource name computation from `deploy.py` into a frozen dataclass:

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

def compute_names(config, account_id) -> ResourceNames:
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

### Resource module example: `api_bridge.py`

```python
# resources/api_bridge.py
"""Lambda function bridge for AgentCore invocation."""

# All boto3 call logic stays the same — just organized differently

def deploy(session, config, names, *, agent_runtime_arn: str) -> dict:
    """Create Lambda bridge function + IAM role + function URL.

    Args:
        agent_runtime_arn: From agentcore.deploy() output — passed by orchestrator.

    Returns:
        State entries for this resource group.
    """
    role_arn = _create_lambda_role(session, names.lambda_role, ...)
    func_info = _create_lambda_function(session, names.lambda_function, role_arn, agent_runtime_arn, ...)

    return {
        "lambda_role_name": names.lambda_role,
        "lambda_role_arn": role_arn,
        "lambda_function_name": func_info["function_name"],
        "lambda_function_arn": func_info["function_arn"],
        "lambda_function_url": func_info["function_url"],
    }

def destroy(session, state, progress) -> None:
    """Delete Lambda bridge function and IAM role."""
    resources = state.get("resources", {})
    # ... delete function, delete role (with try/except per resource) ...

def get_status(session, state) -> list[ResourceStatus]:
    """Return Lambda bridge status."""
    # ...
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
- **Design**: Update `design.md` Section 4 (Component Design) to reflect new module structure and orchestrator data flow. Update Section 1.1 architecture diagram.
- **Tasks**: New sprint with tasks for the migration (see Implementation Plan)

## Alternatives Considered

1. **Generic loop with shared state dict**: Resource modules read/write to a shared `state["resources"]` dict. Rejected because it creates implicit coupling — module B reads keys that module A wrote, but nothing enforces this contract. A typo in a key name silently breaks the pipeline. The explicit-argument approach catches these at the function signature level.

2. **CDK-style dependency graph**: Declare dependencies between resources and let a resolver compute order. Rejected because this is exactly the complexity we're avoiding — graphs can cycle, resolution is opaque, and debugging "why did CDK deploy X before Y" is frustrating. Our flat ordered orchestrator is the feature, not the limitation.

3. **Base class / ABC for resources**: Class hierarchy with `deploy()`/`destroy()` methods. Rejected because plain functions with a naming convention are simpler and sufficient for 5 modules. A class adds ceremony without benefit at this scale.

4. **Keep `aws/` but add an adapter layer**: Wrap existing modules. Rejected because the module boundaries themselves are the problem — `lambda_bridge.py` mixes the API bridge and the edge function, which have different lifecycles and dependencies.

## Implementation Plan

1. **Create `naming.py`** — Extract name computation from `deploy.py` into `ResourceNames` dataclass
2. **Create `resources/` directory** with `__init__.py` (resource list + ResourceStatus namedtuple)
3. **Create `resources/_base.py`** — Shared helpers (progress task management, session re-export)
4. **Migrate `agentcore.py`** — Move from `aws/` to `resources/`, add `deploy()`/`destroy()`/`get_status()` that wrap existing boto3 logic
5. **Create `resources/storage.py`** — Move S3 logic from `aws/s3.py`, add facade
6. **Create `resources/api_bridge.py`** — Move Lambda bridge logic (non-edge), add facade with `agent_runtime_arn` kwarg
7. **Create `resources/edge.py`** — Move Lambda@Edge logic, add facade
8. **Create `resources/cdn.py`** — Move CloudFront logic, add facade with explicit cross-resource kwargs
9. **Rewrite `deploy.py`** — Explicit step-by-step orchestration with typed outputs threading
10. **Rewrite `destroy.py`** — Explicit reverse-order steps
11. **Rewrite `status.py`** — Collect status rows from each module
12. **Migrate tests** — Move from `tests/aws/` to `tests/resources/`, update imports
13. **Delete `aws/` directory** and `cf_function.py`
14. **Update `spec/design.md`** — Reflect new structure and data flow
15. **Run full test suite and linter** — Verify no regressions

## Testing Plan

- All existing tests continue to pass (updated imports only)
- Each resource module is independently testable with moto/mock
- Resource modules can be tested in isolation — no cross-module dependencies to set up
- Orchestrator data flow is readable and auditable by inspection
- Backward compatibility: existing `.three-stars-state.json` files still work
- `ruff check` and `ruff format` pass with zero warnings
