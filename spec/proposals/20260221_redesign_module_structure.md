# Proposal: Redesign Module Structure

**Date**: 2026-02-21
**Author**: Claude Agent
**Status**: Approved

**Architect Review**: 2026-02-21
- No deployed resources exist — migration/backward-compatibility is not a concern
- State must use static typing (dataclasses) rather than dynamic dictionary keys

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
three_stars/
├── cli.py              # (unchanged)
├── config.py           # (unchanged)
├── state.py            # Typed DeploymentState + per-resource state dataclasses
├── deploy.py           # Orchestrator: manages order + cross-resource data flow
├── destroy.py          # Orchestrator: reverse order
├── status.py           # Orchestrator: query each resource
├── init.py             # (unchanged)
├── naming.py           # Resource naming (extracted from deploy.py)
└── resources/          # Resource modules (replaces aws/)
    ├── __init__.py     # ResourceStatus namedtuple, re-exports
    ├── _base.py        # Shared helpers (session creation, progress utils)
    ├── agentcore.py    # IAM role + runtime + endpoint + packaging
    ├── storage.py      # S3 bucket + frontend upload
    ├── api_bridge.py   # Lambda function + IAM role + function URL
    ├── edge.py         # Lambda@Edge function + IAM role (us-east-1)
    └── cdn.py          # CloudFront distribution + OACs + bucket policy
```

### Typed state model

Each resource module defines its own state dataclass. The top-level `DeploymentState` composes them as optional fields (each `None` until the resource is deployed). This replaces the dynamic `state["resources"]` dictionary with compile-time-checked attribute access.

```python
# state.py

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
class ApiBridgeState:
    role_name: str
    role_arn: str
    function_name: str
    function_arn: str
    function_url: str

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
    api_bridge: ApiBridgeState | None = None
    edge: EdgeState | None = None
    cdn: CdnState | None = None
```

Serialization uses `dataclasses.asdict()` for saving and reconstruction from nested dicts on load. Since no deployments exist yet, there is no migration concern for this format change.

### Resource module interface

Each module exposes three plain functions with **explicit inputs and outputs**:

```python
# resources/storage.py  (example)

def deploy(session, config, names) -> StorageState:
    """Create or update this resource group.

    Returns:
        Typed state dataclass capturing all resource outputs to persist.
    """

def destroy(session, state: StorageState) -> None:
    """Delete this resource group. Best-effort, logs warnings on failure."""

def get_status(session, state: StorageState) -> list[ResourceStatus]:
    """Return status rows for this resource group."""
```

**Key design rules:**
- `deploy()` receives only what it needs: session, config, names, plus **explicit kwargs for cross-resource data**
- `deploy()` returns a typed state dataclass — the orchestrator assigns it to `DeploymentState.<field>`
- `destroy()` receives the module's own typed state — field access is compile-time checked
- `get_status()` receives the module's own typed state — no key guessing
- Modules **never import each other** — all wiring is in the orchestrator
- Cross-resource outputs are passed as typed function arguments (e.g., `agent_runtime_arn: str`), not dictionary lookups

### Cross-resource data flow (the orchestrator's job)

The deploy orchestrator explicitly passes outputs from earlier resources as inputs to later ones. Each `deploy()` returns a typed dataclass, and cross-resource data flows through typed attribute access:

```python
# deploy.py — the orchestrator IS the dependency manager

def run_deploy(config, profile=None):
    sess = create_session(region=config.region, profile=profile)
    account_id = get_account_id(sess)
    names = compute_names(config, account_id)
    state = load_state(config.project_dir) or DeploymentState(
        version=STATE_VERSION,
        project_name=config.name,
        region=config.region,
        deployed_at=datetime.now(UTC).isoformat(),
    )

    with Progress(...) as progress:

        # 1. AgentCore: no dependencies on other resources
        state.agentcore = agentcore.deploy(sess, config, names)
        save_state(config.project_dir, state)

        # 2. Storage: no dependencies on other resources
        state.storage = storage.deploy(sess, config, names)
        save_state(config.project_dir, state)

        # 3. API Bridge: needs agentcore runtime ARN (typed attribute access)
        state.api_bridge = api_bridge.deploy(sess, config, names,
            agent_runtime_arn=state.agentcore.runtime_arn,
        )
        save_state(config.project_dir, state)

        # 4. Edge: no dependencies on other resources
        state.edge = edge.deploy(sess, names)
        save_state(config.project_dir, state)

        # 5. CDN: needs bucket, lambda URL, edge ARN (all typed)
        state.cdn = cdn.deploy(sess, config, names,
            bucket_name=state.storage.s3_bucket,
            lambda_function_url=state.api_bridge.function_url,
            lambda_function_name=state.api_bridge.function_name,
            edge_function_arn=state.edge.function_arn,
        )
        save_state(config.project_dir, state)

    return state
```

**Why this is better than a generic dict:**
- Cross-resource dependencies use **typed attribute access** (`state.agentcore.runtime_arn`), not string key lookups
- A typo in a field name is caught by linters and type checkers — not silently at runtime
- Reading the orchestrator shows the full data flow at a glance
- Each resource module's `deploy()` return type is self-documenting
- Adding a new resource = add a state dataclass + a module + a step in the orchestrator

### Destroy orchestrator

Destroy is simpler — each module receives only its own typed state. The `None` check naturally handles partially-deployed stacks:

```python
# destroy.py

def run_destroy(project_dir, profile=None, skip_confirm=False):
    state = load_state(project_dir)
    # ... confirmation prompt ...
    sess = create_session(...)

    with Progress(...) as progress:
        # Reverse order: CDN first (depends on others), AgentCore last
        if state.cdn:
            cdn.destroy(sess, state.cdn, progress)
        if state.edge:
            edge.destroy(sess, state.edge, progress)
        if state.api_bridge:
            api_bridge.destroy(sess, state.api_bridge, progress)
        if state.storage:
            storage.destroy(sess, state.storage, progress)
        if state.agentcore:
            agentcore.destroy(sess, state.agentcore, progress)

    delete_state(project_dir)
```

Each module's `destroy()` receives its own typed state (e.g., `CdnState`, `EdgeState`), not the full deployment dict. This eliminates the `resources.get("some_key")` pattern and makes the interface self-documenting.

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

    # Each module receives its own typed state (or is skipped if None)
    for module, module_state in [
        (agentcore, state.agentcore),
        (storage, state.storage),
        (api_bridge, state.api_bridge),
        (edge, state.edge),
        (cdn, state.cdn),
    ]:
        if module_state:
            for row in module.get_status(sess, module_state):
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

from three_stars.state import ApiBridgeState

# All boto3 call logic stays the same — just organized differently

def deploy(session, config, names, *, agent_runtime_arn: str) -> ApiBridgeState:
    """Create Lambda bridge function + IAM role + function URL.

    Args:
        agent_runtime_arn: From agentcore.deploy() output — passed by orchestrator.

    Returns:
        Typed state capturing all resource outputs.
    """
    role_arn = _create_lambda_role(session, names.lambda_role, ...)
    func_info = _create_lambda_function(session, names.lambda_function, role_arn, agent_runtime_arn, ...)

    return ApiBridgeState(
        role_name=names.lambda_role,
        role_arn=role_arn,
        function_name=func_info["function_name"],
        function_arn=func_info["function_arn"],
        function_url=func_info["function_url"],
    )

def destroy(session, state: ApiBridgeState, progress) -> None:
    """Delete Lambda bridge function and IAM role."""
    # Direct attribute access — no .get() guessing
    _delete_function(session, state.function_name)
    _delete_role(session, state.role_name)

def get_status(session, state: ApiBridgeState) -> list[ResourceStatus]:
    """Return Lambda bridge status."""
    # state.function_name — typed, no key lookup
    ...
```

### What stays the same

- **CLI layer** (`cli.py`) — no changes
- **Config** (`config.py`) — no changes
- **boto3 call logic** — the actual AWS API calls move file-to-file but don't change
- **Test mocking approach** — moto/mock patterns remain the same

### What changes

- **State file format** — new typed schema with nested resource objects instead of flat `resources` dict. No migration needed (no existing deployments).
- **`state.py`** — rewritten with dataclass-based `DeploymentState` and per-resource state types. `load_state()` returns `DeploymentState` (not `dict`). `save_state()` accepts `DeploymentState` (not `dict`).

### What gets removed

- `aws/cf_function.py` — unused dead code, delete it
- `aws/__init__.py` — replaced by `resources/__init__.py`
- The `aws/` directory entirely — replaced by `resources/`

## Impact

- **Requirements**: No change (same CLI behavior, same resource set)
- **Design**: Update `design.md` Section 4 (Component Design) to reflect new module structure and orchestrator data flow. Update Section 1.1 architecture diagram.
- **Tasks**: New sprint with tasks for the migration (see Implementation Plan)

## Alternatives Considered

1. **Generic loop with shared state dict**: Resource modules read/write to a shared `state["resources"]` dict. Rejected because it creates implicit coupling — module B reads keys that module A wrote, but nothing enforces this contract. A typo in a key name silently breaks the pipeline. The typed dataclass approach catches these at the type-checker level, and the explicit-argument approach catches cross-resource dependencies at the function signature level.

2. **CDK-style dependency graph**: Declare dependencies between resources and let a resolver compute order. Rejected because this is exactly the complexity we're avoiding — graphs can cycle, resolution is opaque, and debugging "why did CDK deploy X before Y" is frustrating. Our flat ordered orchestrator is the feature, not the limitation.

3. **Base class / ABC for resources**: Class hierarchy with `deploy()`/`destroy()` methods. Rejected because plain functions with a naming convention are simpler and sufficient for 5 modules. A class adds ceremony without benefit at this scale.

4. **Keep `aws/` but add an adapter layer**: Wrap existing modules. Rejected because the module boundaries themselves are the problem — `lambda_bridge.py` mixes the API bridge and the edge function, which have different lifecycles and dependencies.

## Implementation Plan

1. **Rewrite `state.py`** — Define typed state dataclasses (`DeploymentState`, `AgentCoreState`, `StorageState`, `ApiBridgeState`, `EdgeState`, `CdnState`). Update `load_state()`/`save_state()` for dataclass serialization.
2. **Create `naming.py`** — Extract name computation from `deploy.py` into `ResourceNames` dataclass
3. **Create `resources/` directory** with `__init__.py` (`ResourceStatus` namedtuple, re-exports)
4. **Create `resources/_base.py`** — Shared helpers (session creation, progress utils)
5. **Migrate `agentcore.py`** — Move from `aws/` to `resources/`, add `deploy() -> AgentCoreState` / `destroy(AgentCoreState)` / `get_status(AgentCoreState)` that wrap existing boto3 logic
6. **Create `resources/storage.py`** — Move S3 logic from `aws/s3.py`, return `StorageState`
7. **Create `resources/api_bridge.py`** — Move Lambda bridge logic (non-edge), return `ApiBridgeState` with `agent_runtime_arn` kwarg
8. **Create `resources/edge.py`** — Move Lambda@Edge logic, return `EdgeState`
9. **Create `resources/cdn.py`** — Move CloudFront logic, return `CdnState` with explicit cross-resource kwargs
10. **Rewrite `deploy.py`** — Explicit step-by-step orchestration using typed state assignment
11. **Rewrite `destroy.py`** — Explicit reverse-order steps with typed per-module state
12. **Rewrite `status.py`** — Collect status rows from each module with typed state
13. **Migrate tests** — Move from `tests/aws/` to `tests/resources/`, update imports and state assertions to use dataclasses
14. **Delete `aws/` directory** and `cf_function.py`
15. **Update `spec/design.md`** — Reflect new structure, typed state model, and data flow
16. **Run full test suite and linter** — Verify no regressions

## Testing Plan

- All existing tests continue to pass (updated imports and state assertions)
- Each resource module is independently testable with moto/mock
- Resource modules can be tested in isolation — no cross-module dependencies to set up
- State dataclass serialization/deserialization round-trips correctly
- Orchestrator data flow is readable and auditable by inspection
- `ruff check` and `ruff format` pass with zero warnings
