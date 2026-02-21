# Proposal: DX Review — Module Redesign Gaps

**Date**: 2026-02-21
**Author**: Developer Experience Review
**Status**: Approved
**Reviewed Proposal**: `20260221_redesign_module_structure.md`

## Background

The approved module redesign proposal improves internal code structure (typed state,
resource modules, explicit orchestrator). However, four developer experience gaps
remain unaddressed. This review documents each gap with evidence from the current
codebase and proposes concrete changes.

---

## Gap 1: Application Name and Resource Tagging

### Question

> How can a developer specify the application name? It should be reflected in
> resource tags to monitor costs.

### Current Behavior

The application name is the `name` field in `three-stars.yml`:

```yaml
name: my-ai-app
```

This is used to generate a resource prefix via `get_resource_prefix()` in
`config.py:168`:

```python
def get_resource_prefix(config: ProjectConfig) -> str:
    return f"sss-{config.name}"
```

All resource names derive from this prefix (S3 bucket, IAM roles, Lambda
functions, AgentCore runtime). The prefix is visible in the AWS console through
resource names, but **no AWS resource tags are applied anywhere in the codebase**.

### The Gap

There are zero calls to `TagResource`, `tag_resource`, `put_bucket_tagging`, or
`Tags` parameters in any AWS create call across the entire `aws/` directory.
This means:

- **No cost allocation** — AWS Cost Explorer cannot group costs by application
- **No resource identification** — When multiple three-stars apps share an
  account, there's no way to filter by application in the console
- **No governance** — Tag-based IAM policies, AWS Config rules, and
  organization-wide tag policies cannot apply
- **No automation** — Resource groups, cleanup scripts, and monitoring filters
  cannot target resources by application

### Recommendation

Add a standard tag set to every taggable resource. The tags should be applied
inside each resource module's `deploy()` function.

**Standard tags** (applied automatically):

| Tag Key | Value | Purpose |
|---------|-------|---------|
| `three-stars:project` | `config.name` | Cost allocation, filtering |
| `three-stars:managed-by` | `three-stars` | Identify tool-managed resources |
| `three-stars:region` | `config.region` | Cross-region identification |

**Design approach:**

1. Add a `tags: dict[str, str]` field to `ProjectConfig` (populated from
   `three-stars.yml` with the standard tags above merged in)
2. Pass `tags` to each resource module's `deploy()` via the orchestrator
3. Each module applies tags to its own resources using the appropriate AWS API:
   - S3: `put_bucket_tagging()`
   - IAM: `tag_role()`
   - Lambda: `Tags` parameter on `create_function()`
   - CloudFront: `Tags` parameter on `create_distribution()`
   - AgentCore: `tags` parameter if supported, otherwise skip gracefully

**Config extension** (optional custom tags):

```yaml
name: my-ai-app
tags:
  team: platform
  environment: staging
  cost-center: "12345"
```

Custom tags are merged with standard tags. Standard tags take precedence to
prevent accidental overrides.

**Impact on the redesign proposal:**

- `naming.py`: No change — tags are separate from naming
- Resource module `deploy()` signatures: Add `tags: dict[str, str]` parameter
- Orchestrator: Compute merged tag dict once, pass to all modules
- State: No change — tags don't need to be persisted (recomputed from config)

---

## Gap 2: Rollback

### Question

> How can a developer roll back a deployment?

### Current Behavior

There is no rollback mechanism. The current deployment model is:

1. `deploy` saves state after each resource creation (`save_state()` on
   `deploy.py:55`, `63`, `108`, `136`, `148`, `161`, `168`, `211`)
2. If deploy fails mid-way, the state file reflects the partially-deployed stack
3. Recovery requires running `destroy` to clean everything up, then `deploy`
   again
4. There is no concept of a "previous version" to restore

The requirements spec (`REQ-NF-031`) states "The CLI shall support partial
rollback if deployment fails mid-way" — this is not implemented.

### Analysis of What "Rollback" Means for Each Resource

| Resource | Rollback complexity | Notes |
|----------|-------------------|-------|
| **S3 (frontend)** | Low — re-upload previous files | Files are overwritten on each deploy. Previous version is only on developer's disk. |
| **AgentCore runtime** | Low — update agent code | But the current code *skips* updates entirely (see Gap 3). |
| **Lambda bridge** | Low — update function code | Already has update-in-place logic. |
| **Lambda@Edge** | Medium — version propagation takes time | CloudFront replicas must drain. |
| **CloudFront** | High — configuration updates take 5-10 min | Distribution changes propagate globally. |
| **IAM roles** | N/A — rarely change between deploys | Policies are static. |

### Recommendation

Full transactional rollback is disproportionately complex for a CLI tool
targeting this audience. Instead, define a clear **recovery strategy** with two
tiers:

**Tier 1: "Redeploy previous code" (the common case)**

When a developer wants to undo a code change (agent or frontend), the workflow
is:

```bash
# Revert code changes locally (git, etc.)
git checkout HEAD~1 -- agent/ app/

# Redeploy — only code artifacts are updated
three-stars deploy
```

This requires Gap 3 to be fixed first (agent code must actually update on
redeploy).

**Tier 2: "Clean slate" (infrastructure is broken)**

When infrastructure is in a bad state:

```bash
three-stars destroy --yes
three-stars deploy
```

**What the proposal should add:**

1. **Document the recovery model** — Add a "Recovery" section to the CLI help
   and the README explaining both tiers
2. **Make `deploy` truly idempotent** — Every resource module's `deploy()` must
   handle "resource already exists" by updating in place (see Gap 3). This is
   the prerequisite for Tier 1 rollback.
3. **Add `--force` flag to `deploy`** — Force full re-creation of all resources
   even if state says they exist. This handles edge cases where AWS state and
   local state diverge.
4. **State backup before deploy** — Before mutating state, copy the current
   `.three-stars-state.json` to `.three-stars-state.json.bak`. This allows
   manual recovery if the new state is corrupted.

**What the proposal should NOT add** (not yet):

- Versioned state history (over-engineering for MVP)
- Automatic rollback on failure (complex, error-prone, better to let the
  developer decide)
- Blue/green deployments (future feature, not MVP)

---

## Gap 3: Updating Agent and Frontend Code

### Question

> How can a developer update agent and frontend code? Is it faster than initial
> deployment?

### Current Behavior — Frontend

Frontend updates work correctly. On every `deploy`, S3 files are re-uploaded
(`deploy.py:68-72`):

```python
file_count = s3.upload_directory(sess, bucket_name, app_path)
```

This is unconditional — files are always uploaded. CloudFront serves stale
content until the cache expires or is invalidated (no invalidation is triggered).

### Current Behavior — Agent Code

**Agent code updates are broken.** On redeploy, `deploy.py:86-93` skips the
runtime entirely if it already exists:

```python
existing_runtime_id = state["resources"].get("agentcore_runtime_id")
if existing_runtime_id:
    # Skips creation — but also skips any update
    runtime = {
        "runtime_id": existing_runtime_id,
        "runtime_arn": state["resources"].get("agentcore_runtime_arn", ""),
    }
```

This means: **a developer changes their agent code, runs `deploy` again, and
nothing happens**. The old agent code continues running. This is a critical DX
bug.

### Current Behavior — Lambda Bridge

Lambda bridge handles updates correctly. `lambda_bridge.py` catches
`ResourceConflictException` on `create_function()` and falls back to
`update_function_code()` + `update_function_configuration()`.

### Current Behavior — Lambda@Edge, CloudFront

Both are skipped if they already exist (same pattern as AgentCore). Since their
configuration rarely changes, this is acceptable for now but should be
revisited.

### Recommendation

**Each resource module's `deploy()` must implement create-or-update logic.** The
redesign proposal should mandate this pattern:

```python
def deploy(session, config, names, **kwargs) -> StateDataclass:
    """Create or update this resource group."""
    # Try to detect existing resource
    # If exists: update in place
    # If not: create from scratch
    # Return current state either way
```

**Specific fixes needed per module:**

| Module | Current | Required |
|--------|---------|----------|
| `agentcore` | Skips if exists | Must re-package + update runtime code via AgentCore API |
| `storage` | Re-uploads files (correct) | Add CloudFront invalidation trigger (return a flag) |
| `api_bridge` | Updates code on conflict (correct) | No change needed |
| `edge` | Skips if exists | Update function code if source changes |
| `cdn` | Skips if exists | Update distribution config if settings change |

**Update speed vs. initial deployment:**

| Operation | Initial Deploy | Update |
|-----------|---------------|--------|
| IAM roles | ~5s (create) | ~0s (skip, unchanged) |
| S3 upload | ~5s | ~5s (re-upload) |
| AgentCore runtime | ~2-5 min (create + wait) | ~30s (update code only) |
| Lambda bridge | ~10s (create) | ~5s (update code) |
| Lambda@Edge | ~10s (create) | ~0s (skip unless changed) |
| CloudFront | ~5-10 min (create + propagate) | ~0s (skip unless config changes) |
| **Total** | **~10-15 min** | **~45s** |

Updates should be substantially faster because they skip infrastructure creation
(IAM roles, CloudFront distribution) and only update code artifacts.

**Impact on the redesign proposal:**

- Each resource module `deploy()` must accept an optional existing state
  parameter to detect update vs. create: `deploy(session, config, names, *,
  existing: StateDataclass | None = None, **kwargs)` — or use a
  try/create/catch/update pattern internally
- The orchestrator passes existing state when available:
  `state.agentcore = agentcore.deploy(sess, config, names,
  existing=state.agentcore)`
- The `StorageState` should include a `needs_invalidation: bool` field (or
  the orchestrator should trigger invalidation after frontend upload + CDN
  exists)

---

## Gap 4: Deployment Progress Monitoring and Error Mitigation

### Question

> How can a developer monitor deployment progress, and how to mitigate failures?

### Current Behavior

Progress is displayed via Rich spinner + text updates in the terminal:

```
⠋ Creating AgentCore IAM role...
✓ AgentCore IAM role ready
⠋ Creating S3 bucket...
✓ S3 bucket ready
...
```

This works for interactive use but has limitations:

1. **No structured output** — CI/CD pipelines cannot parse progress
2. **No elapsed time** — Developer doesn't know how long each step has taken or
   how long to expect
3. **No total progress** — No "step 3 of 7" indicator
4. **Hang detection** — If AgentCore creation hangs, the spinner just keeps
   spinning with no timeout
5. **Error recovery guidance** — When a step fails, the error message is the raw
   AWS exception with no guidance on what to do

### Recommendation

**A. Improve interactive progress display:**

Add step numbering and elapsed time to the progress output:

```
[1/5] Creating AgentCore resources...          (elapsed: 2m 15s)
[2/5] Creating S3 storage...                   (elapsed: 3s)
[3/5] Creating Lambda API bridge...            (elapsed: 8s)
[4/5] Creating Lambda@Edge function...         (elapsed: 5s)
[5/5] Creating CloudFront distribution...      (elapsed: 4m 30s)
```

The step count (5) matches the resource module count — another benefit of the
redesign's clean module boundaries.

**B. Add timeout handling for long-running operations:**

| Operation | Suggested timeout | Mitigation on timeout |
|-----------|------------------|----------------------|
| AgentCore runtime creation | 10 min | Print status, suggest `sss status` to check later |
| CloudFront distribution | 15 min | Print distribution ID, explain propagation delay |
| Lambda@Edge replication | 5 min | Print warning about replica propagation |

On timeout, do NOT destroy partially-created resources. Save state and print
a recovery message:

```
⚠ AgentCore runtime creation timed out after 10 minutes.
  State has been saved. To check progress:
    sss status
  To retry deployment:
    sss deploy
  To clean up:
    sss destroy
```

**C. Improve error messages with actionable guidance:**

Map common AWS errors to developer-friendly messages:

| Error | Current output | Proposed output |
|-------|---------------|-----------------|
| `AccessDeniedException` | Raw exception | "Permission denied. Ensure your AWS credentials have the required permissions. See: [docs link]" |
| `ResourceNotFoundException` | Raw exception | "Resource not found. Your local state may be out of sync. Run `sss destroy` and `sss deploy` to rebuild." |
| `LimitExceededException` | Raw exception | "AWS service limit reached. Request a quota increase in the AWS console for [service]." |
| `InvalidParameterValueException` (Lambda@Edge replica) | `(replicas removing)` | "Lambda@Edge replicas are still being removed. Wait a few minutes and try again." |

**D. Add `--verbose` flag for debugging:**

When `--verbose` is passed:
- Print the AWS API call being made (service, operation, key parameters)
- Print response status codes
- Show full error tracebacks instead of summarized messages

This is useful for CI/CD and for filing bug reports.

**E. Post-deployment health check:**

After all resources are created, automatically run the `status` check and
display results. If any resource is not in a healthy state, print a warning
with next steps:

```
✓ All resources created.

Resource Status:
  S3 Bucket          my-app-abc12345  Active
  AgentCore Runtime  rt-123456        Ready
  Lambda Bridge      sss-my-app-api   Active
  CloudFront         E1234567890      InProgress ⚠

⚠ CloudFront distribution is still propagating.
  Your site will be available at https://d1234.cloudfront.net
  in approximately 5-10 minutes.
```

**Impact on the redesign proposal:**

- Orchestrator (`deploy.py`): Add step counter, elapsed time tracking, and
  post-deployment health check
- Resource modules: Return structured errors (not raw exceptions) — or the
  orchestrator catches and maps them
- CLI (`cli.py`): Add `--verbose` flag
- No impact on the resource module interface itself

---

## Summary of Proposed Changes

| Gap | Severity | Proposal Impact | Sprint Impact |
|-----|----------|----------------|---------------|
| 1. Resource tagging | Medium | Add `tags` param to all `deploy()` functions | +1 task per module, +1 config change |
| 2. Rollback strategy | Medium | Document recovery model, add `--force` flag, state backup | +2 tasks (doc + flag), design clarification |
| 3. Agent code updates | **Critical** | Fix create-or-update in all modules (agent code broken today) | +1 task per module (agent is urgent) |
| 4. Progress monitoring | Low | Step counter, timeouts, error mapping, `--verbose` | +3 tasks (progress, errors, verbose) |

### Recommended Priority

1. **Gap 3 first** — Agent code not updating on redeploy is a functional bug.
   Without this fix, the tool is broken for its primary use case (US-002:
   "Iterating on Code").
2. **Gap 1 second** — Tagging is easy to add during module migration and hard to
   retrofit later.
3. **Gap 4 third** — Progress improvements are incremental and can be added
   after the module structure is in place.
4. **Gap 2 last** — Rollback strategy requires Gap 3 to be complete first, and
   is primarily a documentation task.

## Implementation Plan

1. Update the module redesign proposal to mandate create-or-update logic in
   every resource module's `deploy()` (Gap 3)
2. Add `tags` parameter to resource module interface and `tags` config field
   (Gap 1)
3. Add step-numbered progress, timeout handling, and post-deploy health check
   to the orchestrator (Gap 4)
4. Document the recovery/rollback model and add `--force` flag (Gap 2)
5. Update `spec/tasks.md` with new tasks for Gaps 1-4
