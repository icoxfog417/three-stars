# Implementation Tasks

**Project**: three-stars
**Last Updated**: 2026-02-21
**Status**: Sprint 0 - Foundation

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

- ⬜ T01: Create `pyproject.toml` with dependencies (click, rich, boto3, pyyaml, hatchling)
- ⬜ T02: Create `src/three_stars/__init__.py` and package directory structure
- ⬜ T03: Implement `cli.py` with Click command group (deploy/destroy/status/init stubs)
- ⬜ T04: Implement `config.py` — YAML loading, validation, ProjectConfig dataclass
- ⬜ T05: Set up `tests/` with pytest, conftest.py, and config tests
- ⬜ T06: Set up linting (ruff) and formatting in pyproject.toml

## Sprint 1: AWS Core Modules

**Goal**: Individual AWS operations working and tested
**Deliverable**: Each AWS module can create/delete its resource independently

### Tasks

- ⬜ T07: Implement `aws/session.py` — boto3 session creation, account ID lookup
- ⬜ T08: Implement `aws/s3.py` — bucket CRUD, directory upload with MIME types
- ⬜ T09: Implement `aws/cloudfront.py` — distribution CRUD with OAC
- ⬜ T10: Implement `aws/cf_function.py` — CloudFront Function CRUD with JS router template
- ⬜ T11: Implement `aws/agentcore.py` — Runtime CRUD (adapted from toolkit patterns)
- ⬜ T12: Write unit tests with moto mocks for S3/CloudFront; mock boto3 for AgentCore

## Sprint 2: Orchestration

**Goal**: Full deploy/destroy/status workflows working end-to-end
**Deliverable**: `three-stars deploy` creates all resources; `destroy` removes them

### Tasks

- ⬜ T13: Implement `deploy.py` — orchestrate all AWS modules with Rich progress display
- ⬜ T14: Implement `destroy.py` — reverse teardown with confirmation prompt
- ⬜ T15: Implement `status.py` — query resource states, display Rich table
- ⬜ T16: Wire orchestrators into CLI commands (connect deploy.py/destroy.py/status.py to cli.py)
- ⬜ T17: Implement state file read/write (`.three-stars-state.json`)

## Sprint 3: Init Command & Polish

**Goal**: Complete CLI with init command, error handling, and polished UX
**Deliverable**: Full user workflow from init to destroy works

### Tasks

- ⬜ T18: Create `templates/starter/` with minimal project template (config, frontend, agent)
- ⬜ T19: Implement `init` command — copy template, substitute project name
- ⬜ T20: Add comprehensive error handling (missing credentials, invalid config, permission errors)
- ⬜ T21: Add `--yes`, `--region`, `--profile` CLI flag support
- ⬜ T22: Integration tests for CLI commands (end-to-end with moto)

## Sprint 4: Documentation & Release

**Goal**: Ready to publish and use
**Deliverable**: Documented, tested, installable package

### Tasks

- ⬜ T23: Update README.md with installation, quick start, configuration reference
- ⬜ T24: Update spec files with final implementation details
- ⬜ T25: Add GitHub Actions CI workflow (lint, test, build)

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
│       ├── deploy.py
│       ├── destroy.py
│       ├── status.py
│       └── aws/
│           ├── __init__.py
│           ├── session.py
│           ├── s3.py
│           ├── cloudfront.py
│           ├── cf_function.py
│           └── agentcore.py
├── tests/
│   ├── conftest.py
│   ├── test_cli.py
│   ├── test_config.py
│   ├── test_deploy.py
│   └── aws/
│       ├── test_agentcore.py
│       ├── test_s3.py
│       ├── test_cloudfront.py
│       └── test_cf_function.py
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
