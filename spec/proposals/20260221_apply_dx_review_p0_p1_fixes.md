# Proposal: Apply P0 and P1 Fixes from AWS DX Review

**Date**: 2026-02-21
**Author**: Claude Agent
**Status**: Implemented

## Background

The end-to-end DX review (`spec/proposals/20260221_dx_review_aws_developer_experience.md`) identified critical mismatches between documentation and implementation that would cause the very first user interaction to fail. This proposal implements all P0 (fix before any public use) and P1 (fix for good DX) items.

## Current Behavior

1. **CLI name**: README documents `three-stars init`, `three-stars deploy`, etc., but the actual entry point is `sss`. First command fails.
2. **Python version**: README says "Python 3.11+" but `pyproject.toml` requires `>=3.12`. Install fails on 3.11.
3. **Architecture**: README claims "CloudFront Functions" routes API requests, but the implementation uses Lambda + Lambda@Edge. The "three stars" listed in README are wrong.
4. **Deploy output**: README shows a 7-line deploy output that doesn't match the actual 5-step numbered progress.
5. **No local testing**: After `sss init`, there's no way to test the agent locally before deploying.

## Proposal

### P0-1: Fix CLI Entry Point in README

Update all references in README.md from `three-stars` to `sss`. The `sss` command is already shipped and changing the entry point would break existing users. Instead, the README should accurately document the actual CLI name.

Also add `three-stars` as a secondary entry point in `pyproject.toml` so both work.

### P0-2: Fix Python Version in README

Change README prerequisites from "Python 3.11+" to "Python 3.12+". The `pyproject.toml` already requires `>=3.12` and the code uses the flat layout which benefits from 3.12 tooling. The `.python-version` file already says `3.12`.

### P0-3: Fix Architecture Description

Rewrite the "three stars" description and architecture diagram:
- Star 1: **Amazon Bedrock AgentCore** — AI agent backend (unchanged)
- Star 2: **Amazon CloudFront + S3** — Frontend CDN (unchanged)
- Star 3: **Lambda API Bridge** — Routes `/api/*` requests to AgentCore (was: "CloudFront Functions")

Update the architecture ASCII diagram to show Lambda instead of CloudFront Functions.

### P0-4: Update Deploy Output in README

Replace the fictional 7-line deploy output with the actual 5-step progress output matching `deploy.py`.

### P1-1: Reframe "3 Stars" Brand

Update the opening description to frame the three stars as conceptual layers (backend, frontend, bridge) rather than literal AWS resource names. This is truthful about the deployment while preserving the brand.

### P1-2: Add Local Testing to Agent Template

Add `if __name__ == "__main__":` block to the starter `agent.py` template so developers can test locally:
```python
if __name__ == "__main__":
    result = handler({"message": "Hello!"})
    print(result["message"])
```

### P1-3: Update Spec Files

Update `spec/requirements.md` and `spec/design.md` to replace all "CloudFront Functions" references with the actual Lambda architecture, and fix Python version references.

## Impact

- **Requirements**: Fix REQ-DEPLOY-005 (says "CloudFront Function"), fix constraint "Python 3.11+" → "Python 3.12+", fix user stories referencing `three-stars` CLI
- **Design**: Fix Section 1.1 architecture diagram comment (already correct), fix Section 7 CLI entry point reference
- **Tasks**: New Sprint 6 with 6 tasks for the fixes

## Alternatives Considered

1. **Rename `sss` to `three-stars`**: Rejected — `sss` is already in the entry point, the `pyproject.toml` scripts section, and users may already have it installed. Instead, add `three-stars` as a second entry point.
2. **Lower Python to 3.11**: Rejected — the project already targets 3.12 in `.python-version` and `pyproject.toml`. Lowering creates maintenance burden for no benefit.
3. **Actually use CloudFront Functions**: Rejected — CloudFront Functions cannot make network calls, so Lambda is required for the AgentCore bridge.

## Implementation Plan

1. Update `pyproject.toml`: add `three-stars` as additional entry point
2. Rewrite `README.md`: fix CLI name, Python version, architecture, deploy output, "3 stars" brand
3. Update `templates/starter/agent/agent.py`: add local testing block
4. Update `spec/requirements.md`: fix CloudFront Function refs, Python version
5. Update `spec/design.md`: fix CLI entry point reference in Section 7
6. Update `spec/tasks.md`: add Sprint 6 tasks
7. Run tests and linter to verify no regressions

## Testing Plan

- `sss --help` still works (existing entry point preserved)
- `three-stars --help` works (new entry point)
- `sss init test-app` scaffolds project with local-testable agent
- `python agent/agent.py` runs locally without errors (assuming boto3 installed but no AWS creds — should show error message, not crash)
- All 59 existing tests pass
- `ruff check` passes
