---
name: test-dx
description: Run an end-to-end developer experience test of three-stars. Installs, inits, deploys, updates, and destroys a project in .sandbox/, then produces a structured DX report.
argument-hint: [optional: focus area e.g. "deploy speed" or "destroy cleanup"]
context: current
allowed-tools: Read, Grep, Glob, Bash, Write, Edit, WebSearch, WebFetch, AskUserQuestion
---

Test the three-stars developer experience end-to-end: $ARGUMENTS

## Overview

You are a **developer experience evaluator**. Your job is to walk through the full three-stars lifecycle as a new developer would, recording timings, output quality, errors, and surprises. Work entirely inside the project's `.sandbox/test-dx/` directory.

**IMPORTANT**: The project root is the directory containing this skill file's parent `.claude/` directory. All `.sandbox/` paths are relative to the project root. Install three-stars from the project root using `uv pip install -e <project-root>`.

## Test Procedure

### Phase 0: Setup

```bash
cd <project-root>
mkdir -p .sandbox/test-dx
cd .sandbox/test-dx
uv init
uv venv && uv pip install -e <project-root>
```

Verify:
- `sss --help` prints usage
- `sss --version` prints a version number

### Phase 1: Init (Scaffold)

```bash
cd .sandbox/test-dx
source .venv/bin/activate
sss init my-test-app
```

**Check:**
- [ ] Output lists created files
- [ ] `my-test-app/three-stars.yml` exists and is valid YAML
- [ ] `my-test-app/agent/agent.py` exists
- [ ] `my-test-app/app/index.html` exists
- [ ] "Next steps" guidance is printed
- [ ] Model ID in config is a valid Bedrock model ID

### Phase 2: Deploy — 1st WoW (First Deployment)

```bash
cd my-test-app
time sss deploy -y -v
```

**Check:**
- [ ] Progress output shows step numbers `[1/5]` through `[5/5]`
- [ ] Each step completes with green status
- [ ] Health check table prints with all resources showing Active/Ready/Deployed
- [ ] "Deployed successfully!" message with a URL is printed
- [ ] Recovery commands are shown
- [ ] `curl <URL>` returns 200 with the frontend HTML
- [ ] `curl -X POST <URL>/api/invoke -H "Content-Type: application/json" -d '{"message":"hello"}'` returns a streaming agent response

**Record:** Total deploy time, any warnings, any confusing output.

**PAUSE — User Browser Test**: After completing automated checks, use `AskUserQuestion` to show the deployed frontend URL and ask the user to test it in their browser. Present the URL clearly and ask them to confirm the frontend loads and the chat works. Wait for user confirmation before proceeding to Phase 3.

### Phase 3: Update & Redeploy — 2nd WoW (Fast Iteration)

Make a visible change to the frontend (e.g., update `<h1>` text), then:

```bash
time sss deploy -y -v
```

**Check:**
- [ ] "Existing deployment detected — updating resources" message appears
- [ ] Dependency caching: "Using cached dependencies" message
- [ ] Redeploy completes significantly faster than first deploy
- [ ] `curl <URL>` shows the updated content (cache invalidation worked)
- [ ] API still works after update

**Record:** Redeploy time, whether frontend change is immediately visible.

**PAUSE — User Browser Test**: Use `AskUserQuestion` to show the URL again and ask the user to verify the frontend update is visible in their browser (e.g., the changed `<h1>` text). Also ask them to confirm the chat still works. Wait for user confirmation before proceeding.

### Phase 4: Status Check

```bash
sss status
```

**Check:**
- [ ] Status table shows all resources
- [ ] URL is printed
- [ ] No error warnings

### Phase 5: Destroy — Clean Teardown

```bash
sss destroy --yes
```

**Check:**
- [ ] Destroy produces visible output (not silent)
- [ ] "All resources destroyed" confirmation message
- [ ] `sss status` shows "No deployment found"
- [ ] Verify no orphan AWS resources remain:
  - `aws s3 ls | grep sss-{project}`
  - `aws lambda list-functions --region us-east-1 --query "Functions[?starts_with(FunctionName, 'sss-{project}')].FunctionName"`
  - `aws iam list-roles --query "Roles[?starts_with(RoleName, 'sss-{project}')].RoleName"`
  - `aws cloudfront list-distributions --query "DistributionList.Items[?contains(Comment, '{project}')]"`

**Record:** Destroy time, any orphaned resources, any error output.

### Phase 6: Cleanup

```bash
rm -rf .sandbox/test-dx/my-test-app
```

## Report Format

After completing all phases, write a DX report to `.sandbox/test-dx/DX_REPORT.md`:

```markdown
# three-stars DX Test Report

**Date**: {date}
**Tester**: Claude Agent (DX Evaluator)
**three-stars version**: {version}

## Timeline

| Phase | Command | Time | Result | Notes |
|-------|---------|------|--------|-------|
| Install | `uv pip install ...` | Xs | OK/FAIL | ... |
| Init | `sss init` | Xs | OK/FAIL | ... |
| Deploy | `sss deploy -y -v` | Xs | OK/FAIL | ... |
| Update | edit + `sss deploy -y -v` | Xs | OK/FAIL | ... |
| Status | `sss status` | Xs | OK/FAIL | ... |
| Destroy | `sss destroy --yes` | Xs | OK/FAIL | ... |

## 1st WoW: First Deploy

**Rating**: X/10

**Positives:**
- ...

**Issues:**
- ...

## 2nd WoW: Fast Iteration

**Rating**: X/10

**Positives:**
- ...

**Issues:**
- ...

## Destroy: Clean Teardown

**Rating**: X/10

**Positives:**
- ...

**Issues:**
- ...

## Bugs Found

| Severity | Issue | Repro Steps |
|----------|-------|-------------|
| P0 | ... | ... |
| P1 | ... | ... |
| P2 | ... | ... |

## User Browser Test Feedback

**After 1st Deploy:**
- Frontend loaded: YES/NO
- Chat worked: YES/NO
- User comments: ...

**After Update:**
- Updated content visible: YES/NO
- Chat still worked: YES/NO
- User comments: ...

## DX Recommendations

1. ...
2. ...

## Comparison Notes

| Aspect | three-stars | CDK / SAM |
|--------|------------|-----------|
| ... | ... | ... |
```

## Guidelines

- **Be honest**: Report exactly what you see, including rough edges
- **Time everything**: Use `time` command for all operations
- **Test as a newcomer**: Don't skip steps or use insider knowledge
- **Screenshot mental model**: Describe what a developer would think/feel at each step
- **Check AWS residuals**: After destroy, verify no orphan resources remain
- **Note warning/error output**: Any `RequestsDependencyWarning`, stack traces, or confusing messages
- **Compare to README**: Flag any mismatches between documented and actual behavior
