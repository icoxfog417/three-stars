# Implementation Tasks

**Project**: three-stars
**Last Updated**: 2026-02-21
**Status**: Sprint 5 - Module Structure Redesign

## Task Status Legend

| Icon | Status | Meaning |
|------|--------|---------|
| ⬜ | TODO | Not started, available for work |
| 🔄 | IN PROGRESS | Currently being worked on |
| ✅ | DONE | Completed |
| 🚫 | BLOCKED | Waiting on dependency |
| ⏸️ | ON HOLD | Paused |

## Key Principles

- Work in vertical slices (end-to-end features)
- Verify unknowns in `.sandbox/` before production implementation
- Create proposals before changing spec files
- Keep tasks small and independently testable

## Sprint 0: Foundation & Setup

**Goal**: Working Python package with CLI skeleton and test infrastructure
**Deliverable**: `pip install -e .` works, `three-stars --help` shows commands

### Tasks

- ✅ T01: Create `pyproject.toml` with dependencies (click, rich, boto3, pyyaml, hatchling)
- ✅ T02: Create `src/three_stars/__init__.py` and package directory structure
- ✅ T03: Implement `cli.py` with Click command group (deploy/destroy/status/init stubs)
- ✅ T04: Implement `config.py` — YAML loading, validation, ProjectConfig dataclass
- ✅ T05: Set up `tests/` with pytest, conftest.py, and config tests
- ✅ T06: Set up linting (ruff) and formatting in pyproject.toml

## Sprint 1: AWS Core Modules

**Goal**: Individual AWS operations working and tested
**Deliverable**: Each AWS module can create/delete its resource independently

### Tasks

- ✅ T07: Implement `aws/session.py` — boto3 session creation, account ID lookup
- ✅ T08: Implement `aws/s3.py` — bucket CRUD, directory upload with MIME types
- ✅ T09: Implement `aws/cloudfront.py` — distribution CRUD with OAC
- ✅ T10: Implement `aws/cf_function.py` — CloudFront Function CRUD with JS router template
- ✅ T11: Implement `aws/agentcore.py` — Runtime CRUD (adapted from toolkit patterns)
- ✅ T12: Write unit tests with moto mocks for S3/CloudFront; mock boto3 for AgentCore

## Sprint 2: Orchestration

**Goal**: Full deploy/destroy/status workflows working end-to-end
**Deliverable**: `three-stars deploy` creates all resources; `destroy` removes them

### Tasks

- ✅ T13: Implement `deploy.py` — orchestrate all AWS modules with Rich progress display
- ✅ T14: Implement `destroy.py` — reverse teardown with confirmation prompt
- ✅ T15: Implement `status.py` — query resource states, display Rich table
- ✅ T16: Wire orchestrators into CLI commands (connect deploy.py/destroy.py/status.py to cli.py)
- ✅ T17: Implement state file read/write (`.three-stars-state.json`)

## Sprint 3: Init Command & Polish

**Goal**: Complete CLI with init command, error handling, and polished UX
**Deliverable**: Full user workflow from init to destroy works

### Tasks

- ✅ T18: Create `templates/starter/` with minimal project template (config, frontend, agent)
- ✅ T19: Implement `init` command — copy template, substitute project name
- ✅ T20: Add comprehensive error handling (missing credentials, invalid config, permission errors)
- ✅ T21: Add `--yes`, `--region`, `--profile` CLI flag support
- ✅ T22: Integration tests for CLI commands (end-to-end with moto)

## Sprint 4: Documentation & Release

**Goal**: Ready to publish and use
**Deliverable**: Documented, tested, installable package

### Tasks

- ✅ T23: Update README.md with installation, quick start, configuration reference
- ✅ T24: Update spec files with final implementation details
- ✅ T25: Add GitHub Actions CI workflow (lint, test, build)

## Sprint 5: Module Structure Redesign

**Goal**: Reorganize from service-based `aws/` modules to resource-based `resources/` modules with typed state
**Deliverable**: Same CLI behavior, but with typed `DeploymentState`, resource modules with `deploy()`/`destroy()`/`get_status()`, and explicit orchestrator data flow
**Proposal**: `spec/proposals/20260221_redesign_module_structure.md`

### Tasks

- ⬜ T26: Rewrite `state.py` — Define typed state dataclasses (`DeploymentState`, `AgentCoreState`, `StorageState`, `ApiBridgeState`, `EdgeState`, `CdnState`). Update `load_state()`/`save_state()` for dataclass serialization via `dataclasses.asdict()`.
- ⬜ T27: Create `naming.py` — Extract resource name computation from `deploy.py` into `ResourceNames` frozen dataclass with `compute_names()` function.
- ⬜ T28: Create `resources/` package — `__init__.py` with `ResourceStatus` namedtuple; `_base.py` with shared helpers (session creation re-export, progress utils).
- ⬜ T29: Create `resources/agentcore.py` — Move IAM role + runtime + endpoint logic from `aws/agentcore.py`. Expose `deploy() -> AgentCoreState`, `destroy(AgentCoreState)`, `get_status(AgentCoreState)`.
- ⬜ T30: Create `resources/storage.py` — Move S3 bucket + upload logic from `aws/s3.py`. Expose `deploy() -> StorageState`, `destroy(StorageState)`, `get_status(StorageState)`.
- ⬜ T31: Create `resources/api_bridge.py` — Move Lambda bridge logic from `aws/lambda_bridge.py` (non-edge). Expose `deploy(*, agent_runtime_arn: str) -> ApiBridgeState`, `destroy(ApiBridgeState)`, `get_status(ApiBridgeState)`.
- ⬜ T32: Create `resources/edge.py` — Move Lambda@Edge logic from `aws/lambda_bridge.py`. Expose `deploy() -> EdgeState`, `destroy(EdgeState)`, `get_status(EdgeState)`.
- ⬜ T33: Create `resources/cdn.py` — Move CloudFront logic from `aws/cloudfront.py`. Expose `deploy(*, bucket_name, lambda_function_url, lambda_function_name, edge_function_arn) -> CdnState`, `destroy(CdnState)`, `get_status(CdnState)`.
- ⬜ T34: Rewrite `deploy.py` — Step-by-step orchestration using typed state assignment (`state.agentcore = agentcore.deploy(...)`) with cross-resource data threading via typed attribute access.
- ⬜ T35: Rewrite `destroy.py` — Reverse-order teardown, each module receives its own typed state. `None` check for partially-deployed stacks.
- ⬜ T36: Rewrite `status.py` — Each module's `get_status()` receives its own typed state.
- ⬜ T37: Migrate tests from `tests/aws/` to `tests/resources/`. Update imports and state assertions to use typed dataclasses.
- ⬜ T38: Delete `aws/` directory (including unused `cf_function.py`).
- ⬜ T39: Run full test suite and linter — verify zero regressions, `ruff check` and `ruff format` pass.

## Backlog

Items not yet scheduled:

- ⬜ Custom domain name support (ACM + Route53)
- ⬜ Multiple environment support (dev/staging/prod)
- ⬜ Streaming agent responses (SSE/WebSocket)
- ⬜ Cost estimation command
- ⬜ `three-stars logs` command for agent logs

## Reference

### Project Structure

```
three-stars/
├── pyproject.toml
├── src/
│   └── three_stars/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── state.py           # Typed DeploymentState + per-resource state dataclasses
│       ├── naming.py          # ResourceNames frozen dataclass
│       ├── deploy.py          # Orchestrator with typed state
│       ├── destroy.py         # Reverse-order with typed per-module state
│       ├── status.py          # Status with typed per-module state
│       ├── init.py
│       └── resources/         # Resource modules (replaces aws/)
│           ├── __init__.py
│           ├── _base.py
│           ├── agentcore.py
│           ├── storage.py
│           ├── api_bridge.py
│           ├── edge.py
│           └── cdn.py
├── tests/
│   ├── conftest.py
│   ├── test_cli.py
│   ├── test_config.py
│   ├── test_deploy.py
│   └── resources/
│       ├── test_agentcore.py
│       ├── test_storage.py
│       ├── test_api_bridge.py
│       ├── test_edge.py
│       └── test_cdn.py
├── templates/
│   └── starter/
│       ├── three-stars.yml
│       ├── app/
│       │   └── index.html
│       └── agent/
│           ├── requirements.txt
│           └── agent.py
└── spec/
```

### Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| click | >=8.1 | CLI framework |
| rich | >=13.0 | Terminal UX |
| boto3 | >=1.35 | AWS SDK |
| pyyaml | >=6.0 | Config parsing |
| pytest | >=8.0 | Testing (dev) |
| moto | >=5.0 | AWS mocks (dev) |
| ruff | >=0.9 | Linting (dev) |
