# Proposal: DX Refinement from 2026-02-22 E2E Test

**Date**: 2026-02-22
**Author**: Claude Agent
**Status**: Proposed

## Background

A second full E2E developer experience test was conducted on 2026-02-22 after applying the DX survey fixes (destroy summary table, `TimeElapsedColumn`, step labels, `--verbose` on destroy, `RequestsDependencyWarning` suppression). The test confirmed major improvements (rating up from 6.5 to ~8/10) but surfaced four remaining bugs and eight enhancement recommendations. This proposal covers the actionable fixes for the 0.1.0 release.

## Current Behavior

### What's working well after recent fixes
- Deploy: 5-step numbered progress with elapsed time, health check table, recovery commands
- Redeploy: 21s cached deploys with automatic cache invalidation
- Destroy: summary table, `TimeElapsedColumn`, step labels, `--verbose` flag
- Streaming SSE responses work out of the box
- `RequestsDependencyWarning` suppressed in CLI

### Remaining issues

1. **Deploy progress steps invisible**: The `[1/5]` through `[5/5]` labels exist in code (`deploy.py:35-36`) but `progress.remove_task(task)` clears each step before the user can read it. With a fast terminal, developers see only spinner flicker — no sense of progress during a 4-minute deploy.

2. **Lambda@Edge orphaned after destroy**: `edge.destroy()` returns `deleted=False` when replicas are still cleaning up. The function and IAM role remain in AWS. The destroy summary shows "Pending replica cleanup" but no follow-up instructions.

3. **`--verbose` on deploy adds no visible output**: `deploy.py` only prints one extra line (`verbose and state.agentcore` at line 93-94) which only triggers on redeploy of AgentCore. First deploy with `-v` looks identical to without it.

4. **Recovery command assumes git**: `cli.py:72` prints `git checkout HEAD~1 -- agent/ app/ && sss deploy` which fails if the project isn't in a git repo.

## Proposal

### Fix 1: Keep completed deploy steps visible (P2)

**File**: `three_stars/deploy.py`

**Problem**: `progress.remove_task(task)` after each step makes completed steps disappear. The Rich Progress bar shows only the currently-active step.

**Option A — Remove `remove_task()` calls** (recommended):
Let completed steps persist in the progress display. Rich shows completed tasks as finished (no spinner). This matches the behavior of tools like `docker compose up` and `terraform apply` where each step stays visible.

```python
# Before (current):
progress.update(task, description=_step_label(1, "[green]S3 storage ready"))
progress.remove_task(task)  # <-- step disappears

# After:
progress.update(task, description=_step_label(1, "[green]S3 storage ready"), completed=1, total=1)
# No remove_task — step stays visible with ✓
```

**Option B — Print step summaries outside Progress**:
Use `console.print()` for completed steps and only use the Progress spinner for the active step. This gives more formatting control.

**Recommendation**: Option A is simpler and consistent with the destroy flow (which also uses `remove_task`). Apply the same change to `destroy.py` for consistency.

### Fix 2: Lambda@Edge post-destroy cleanup instructions (P2)

**File**: `three_stars/destroy.py`

**Problem**: When `edge.destroy()` returns `deleted=False`, the user sees "Pending replica cleanup" in the summary table but gets no actionable next step. The function and IAM role stay in the AWS account indefinitely.

**Changes**:
- After the destroy summary table, if Lambda@Edge was not fully deleted, print explicit cleanup instructions:

```python
# After _print_destroy_summary(results):
if any("Pending replica cleanup" in status for _, _, status in results):
    console.print(
        "\n[yellow]Lambda@Edge replicas are still cleaning up.[/yellow]"
        "\nThe function will become deletable in ~15-60 minutes."
        "\nRun this to finish cleanup later:\n"
    )
    console.print(f"  aws lambda delete-function --function-name {edge_fn_name} --region us-east-1")
    console.print(f"  aws iam delete-role --role-name {edge_role_name}")
    console.print()
```

**Future**: Consider a `sss cleanup` command that scans for orphaned resources by tag and attempts deletion. Out of scope for 0.1.0.

### Fix 3: Make `--verbose` meaningful on deploy (P3)

**File**: `three_stars/deploy.py`

**Changes**: When `verbose=True`, print extra context for each step:
- Step 1 (S3): bucket name, number of files uploaded
- Step 2 (AgentCore): runtime ID, package size, IAM role ARN
- Step 3 (Lambda@Edge): function ARN, role ARN
- Step 4 (CloudFront): distribution ID, domain, OAC IDs
- Step 5 (Resource policy): runtime ARN, edge role ARN

Example verbose output:
```
[1/5] S3 storage ready                    0:00:02
  Bucket: sss-my-app-8d8017f4
  Files uploaded: 2
[2/5] AgentCore ready                     0:00:15
  Runtime: rt-abc123 (24.2 MB package)
  IAM Role: arn:aws:iam::123:role/sss-my-app-agentcore-role
```

### Fix 4: Conditional recovery commands (P3)

**File**: `three_stars/cli.py`

**Changes**: Only show the git-based recovery command if the project is in a git repo:

```python
import os

console.print("[dim]Recovery commands:[/dim]")
if os.path.isdir(os.path.join(project_dir, ".git")):
    console.print("[dim]  Revert code: git checkout HEAD~1 -- agent/ app/ && sss deploy[/dim]")
console.print("[dim]  Clean slate: sss destroy --yes && sss deploy[/dim]")
```

## Impact

- **Requirements**: No change — existing REQ-DEPLOY-009 (progress indicator) and REQ-DESTROY-001 (teardown) cover these fixes
- **Design**: Update Section 6 (Error Handling) to document the Lambda@Edge cleanup guidance and verbose output behavior. Update Section 4.5 (Deploy Orchestrator) to note that completed steps remain visible. Update Section 3.1 (CLI Commands) to add `--verbose` to `destroy` flags.
- **Tasks**: Add Sprint 7 tasks (see below)

## Proposed Tasks (Sprint 7)

| ID | Task | Priority | Size |
|----|------|----------|------|
| T70 | Remove `remove_task()` from deploy.py — keep completed steps visible | P2 | S |
| T71 | Remove `remove_task()` from destroy.py — keep completed steps visible | P2 | S |
| T72 | Add Lambda@Edge post-destroy cleanup instructions with AWS CLI commands | P2 | S |
| T73 | Add verbose output to all 5 deploy steps (bucket name, ARNs, package size) | P3 | M |
| T74 | Make git recovery command conditional on `.git` directory existence | P3 | S |
| T75 | Update `spec/design.md` — destroy `--verbose` flag, visible progress steps, cleanup guidance | P3 | S |
| T76 | Run `/test-dx` to verify all fixes | P3 | M |

## Alternatives Considered

1. **Replace Rich Progress with plain `console.print()`**: Rejected — loses the spinner animation and elapsed time for long-running steps (CloudFront propagation). The Progress widget is the right tool, just need to stop removing completed tasks.

2. **Implement automatic Lambda@Edge retry loop in destroy**: Rejected for 0.1.0 — would add 15-60 minutes to destroy time. Better to give users a manual command they can run later. A `sss cleanup` command could automate this in a future release.

3. **Add `--debug` flag separate from `--verbose`**: Rejected — premature for 0.1.0. `--verbose` should cover the common case (seeing resource IDs and ARNs). A `--debug` flag with full boto3 wire logging can be added later if needed.

4. **Skip agent re-upload when only frontend changed**: Noted in the DX report but deferred — requires change detection logic (file hashing, manifest comparison) which adds complexity. The 21s redeploy is acceptable for 0.1.0.

## Implementation Plan

1. Fix `deploy.py` — remove `remove_task()` calls, add verbose output per step
2. Fix `destroy.py` — remove `remove_task()` calls, add Lambda@Edge cleanup instructions
3. Fix `cli.py` — conditional git recovery command
4. Update `spec/design.md` with new behavior
5. Update `spec/tasks.md` with Sprint 7
6. Run `uv run ruff check && uv run pytest` to verify
7. Run `/test-dx` for full E2E validation

## Testing Plan

- `uv run pytest` — all existing tests pass (no behavioral change in resource modules)
- `uv run ruff check three_stars/ tests/` — zero warnings
- Manual: `sss deploy -y -v` shows all 5 steps persisting with ✓ and verbose details
- Manual: `sss destroy --yes` shows cleanup instructions when Lambda@Edge is pending
- Manual: `sss deploy -y` in a non-git project omits the git recovery command
- E2E: `/test-dx` confirms progress visibility and destroy completeness
