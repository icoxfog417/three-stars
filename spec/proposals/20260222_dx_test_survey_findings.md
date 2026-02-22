# Proposal: DX Survey Findings from E2E Test (2026-02-22)

**Date**: 2026-02-22
**Author**: Claude Agent (DX Lead)
**Status**: Proposed

## Background

A fresh end-to-end developer experience test was conducted on 2026-02-22 using the latest `main` branch code. The test followed the full lifecycle: `pip install` → `sss init` → `sss deploy` → update & redeploy → `sss destroy`. This proposal documents findings and proposes fixes, referencing what has already been addressed since the previous evaluation (`20260222_e2e_developer_experience_evaluation.md`).

### What's Improved Since Last Evaluation

The previous evaluation (rated 6.5/10) identified several issues. The current codebase has addressed:

- **CloudFront cache invalidation** — Now implemented in `deploy.py:168` (`cdn.invalidate_cache()` on updates). Frontend changes are immediately visible after redeploy. Confirmed working in this test.
- **AgentCore code update on redeploy** — Agent code is re-packaged and updated. Confirmed working.
- **Dependency caching** — `"Using cached dependencies"` message appears on redeploy, cutting time dramatically.
- **Lambda@Edge direct architecture** — Replaced Lambda API Bridge + Lambda URL with Lambda@Edge SigV4 signing directly to AgentCore. Simpler, more secure.
- **`pyproject.toml` in starter template** — Added for developer convenience.

## Test Results Summary

| Step | Command | Time | Result |
|------|---------|------|--------|
| Install | `uv pip install -e .` | ~2s | OK |
| Init | `sss init my-app` | <1s | OK |
| Deploy (1st) | `sss deploy -y -v` | ~3-4 min | OK — all resources created, URL live |
| Redeploy (2nd) | `sss deploy -y -v` | **23s** | OK — deps cached, frontend update visible |
| Status | `sss status` | <1s | OK |
| Destroy | `sss destroy --yes` | ~2 min | Partial — orphan resources remain |

**Updated Rating: 7.5/10** — Significant improvement from 6.5. Cache invalidation fix and the 23s redeploy are standout improvements.

## Proposal: Remaining Issues and Fixes

### P0: Destroy Leaves Orphan AWS Resources

**Problem**: After `sss destroy --yes`, two resources remain in the AWS account:
- Lambda@Edge function: `sss-my-app-edge-sha256` (State: Active)
- IAM role: `sss-my-app-edge-role`

The code in `destroy.py:119-128` handles this by printing a message about "replicas still cleaning up — function will be auto-removed by AWS", but:
1. AWS does **not** auto-remove Lambda@Edge functions. They become *deletable* after replica cleanup (30-60 min), but the function persists until explicitly deleted.
2. The IAM role is intentionally kept so AWS can finish cleanup, but is never revisited.
3. With `--yes` flag, the Rich progress spinner removes tasks so quickly that the user sees **zero output** — they have no idea resources remain.

**Impact**:
- Requirements: Violates **REQ-DESTROY-001** ("tear down all deployed resources")
- User story **US-003** acceptance criteria: "All 5 resource types are deleted" — fails

**Proposed Fix**:

1. **Add `--verbose` flag to `destroy` command** (consistent with `deploy`):
   ```python
   # In cli.py destroy command
   @click.option("--verbose", "-v", is_flag=True, help="Print detailed progress.")
   ```

2. **Always print a destroy summary**, even with `--yes`:
   ```python
   # At end of run_destroy(), before the "All resources destroyed" message:
   console.print("\n[bold]Destroy summary:[/bold]")
   console.print(f"  S3 bucket:          {'deleted' if not state.storage else 'remaining'}")
   console.print(f"  AgentCore:          {'deleted' if not state.agentcore else 'remaining'}")
   console.print(f"  Lambda@Edge:        {'deleted' if edge_deleted else 'pending cleanup'}")
   console.print(f"  CloudFront:         {'deleted' if not state.cdn else 'remaining'}")
   ```

3. **Add a deferred cleanup mechanism** — attempt Lambda@Edge deletion with retries:
   ```python
   # In edge.destroy(), after initial deletion attempt fails:
   # Try up to 3 times with exponential backoff (30s, 60s, 120s)
   # If still failing, print explicit instructions:
   console.print(
       "[yellow]Lambda@Edge replicas still cleaning up.[/yellow]\n"
       "  Run this command in ~30 minutes to finish cleanup:\n"
       f"  aws lambda delete-function --function-name {state.function_name} --region us-east-1\n"
       f"  aws iam delete-role --role-name {state.role_name}"
   )
   ```

4. **Consider a `sss cleanup` command** that re-attempts deletion of orphaned resources found by tag scanning.

### P1: Destroy Produces No Visible Output with `--yes`

**Problem**: Running `sss destroy --yes` produces zero output. The Rich `Progress` spinner with `remove_task()` clears all lines before the user can see them. A developer would wonder if anything happened.

**Proposed Fix**:
- Replace `progress.remove_task(task)` with keeping completed tasks visible, or
- Print a final summary table (similar to deploy's health check) showing what was destroyed:

```python
# After all destroy phases complete:
table = Table(title="Destroy Summary")
table.add_column("Resource", style="bold")
table.add_column("Status")
for resource, status in destroyed_resources:
    table.add_row(resource, status)
console.print(table)
console.print("\n[bold green]All resources destroyed.[/bold green]")
```

### P1: SSE Stream Leaks Internal Python Object Representations

**Problem**: The agent API (`/api/invoke`) SSE response includes raw Python `repr()` strings:
```
data: "{'data': 'Hi! ', 'delta': {'text': 'Hi! '}, 'agent': <strands.agent.agent.Agent object at 0xffffbbf6a7d0>, ...}"
```

The frontend `extractText()` function filters these out, but:
- API consumers (curl, Postman, custom clients) see garbage data
- Leaks internal memory addresses (minor security concern)
- Makes the API look unfinished

**Impact**:
- Design: API contract in `design.md` does not document these raw events
- Any non-browser client integration will be confused

**Proposed Fix**:
- Filter events server-side in the agent handler or AgentCore runtime wrapper
- Only emit structured JSON events (`messageStart`, `contentBlockDelta`, `contentBlockStop`, `messageStop`, `metadata`)
- Or document the event format and provide a client-side filtering example

### P2: README Deploy Output Doesn't Match Actual CLI Output

**Problem**: README.md shows:
```
[1/5] S3 storage ready
[2/5] AgentCore ready
[3/5] Lambda API bridge ready
[4/5] Lambda@Edge function ready
[5/5] CloudFront distribution created (propagation ~5-10 min)
```

Actual output uses Rich spinner + health check table format. The step numbers exist in the code but the output format is different (spinner, not persistent lines).

**Proposed Fix**: Update README.md to show the actual output format, including the health check table.

### P2: `--verbose` Flag Inconsistency Across Commands

**Problem**: `sss deploy -v` works, but `sss destroy -v` fails with "No such option: -v". The README documents `--verbose` as a general CLI option.

**Proposed Fix**: Add `--verbose` / `-v` to the `destroy` command in `cli.py`.

### P2: `RequestsDependencyWarning` on Every Command

**Problem**: Every `sss` command prints:
```
RequestsDependencyWarning: urllib3 (2.6.3) or chardet (6.0.0.post1)/charset_normalizer (3.4.4) doesn't match a supported version!
```

**Proposed Fix**: Add warning suppression in the CLI entry point:
```python
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning, module="requests")
```

Or pin compatible versions of `urllib3` and `charset-normalizer` in `pyproject.toml`.

### P2: README Model ID Mismatch

**Problem**: README example shows `anthropic.claude-sonnet-4-20250514` but the `sss init` template generates `us.anthropic.claude-sonnet-4-6` (cross-region inference model ID).

**Proposed Fix**: Update README to match the template's model ID, or vice versa. The cross-region ID (`us.anthropic.claude-sonnet-4-6`) is the better default since it works across regions.

## Impact Summary

| Spec File | Changes Needed |
|-----------|---------------|
| `requirements.md` | No changes — existing REQ-DESTROY-001 covers the destroy fix |
| `design.md` | Add destroy summary output description; document SSE event format |
| `tasks.md` | Add new sprint tasks for the fixes above |

## Proposed Tasks (Sprint 7)

| ID | Task | Priority | Estimate |
|----|------|----------|----------|
| T61 | Add destroy summary output (always visible, even with `--yes`) | P1 | S |
| T62 | Add `--verbose` flag to `destroy` command | P2 | S |
| T63 | Add Lambda@Edge deferred cleanup with explicit user instructions | P0 | M |
| T64 | Suppress `RequestsDependencyWarning` in CLI entry point | P2 | S |
| T65 | Update README deploy output to match actual format | P2 | S |
| T66 | Align README model ID with template default | P2 | S |
| T67 | Investigate server-side SSE event filtering for clean API responses | P1 | M |

## Alternatives Considered

### For Lambda@Edge Cleanup
1. **Block and retry in destroy** — Wait up to 30 minutes in a polling loop. Rejected: too slow for interactive use.
2. **Background cleanup daemon** — Spawn a background process. Rejected: too complex, hard to debug.
3. **Tag-based cleanup command** (recommended) — `sss cleanup` scans for orphaned resources by tag and deletes them. Lightweight, explicit, composable.

### For SSE Stream Noise
1. **Fix in agent template** — Wrap `agent.stream_async()` to filter events. Rejected: puts burden on every user's agent code.
2. **Fix in AgentCore runtime wrapper** — Filter at the BedrockAgentCoreApp level. Best option if we control the wrapper.
3. **Document and provide client library** — Accept the raw stream, provide a JS/Python parsing helper. Fallback option.

## Implementation Plan

1. Start with P0 (T63: Lambda@Edge cleanup) — highest user impact
2. Then P1s (T61: destroy summary, T67: SSE filtering)
3. Batch P2s (T62, T64, T65, T66) as a polish pass
4. Update `spec/design.md` after implementation
5. Re-run `/test-dx` skill to verify fixes
