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

**IMPORTANT**: The project root is the directory containing this skill file's parent `.claude/` directory. All `.sandbox/` paths are relative to the project root. Install three-stars from the project root using `uv pip install -e <project-root>`. The Bash tool does NOT preserve `cd` across separate calls — always use absolute paths or chain commands with `&&` in a single Bash call. Use ONE app (`my-test-app`) for the entire test.

## Test Procedure

### Phase 0: Setup

Define absolute paths up front and use them in every Bash call:

```bash
PROJECT_ROOT=<absolute-project-root>
SANDBOX=$PROJECT_ROOT/.sandbox/test-dx
mkdir -p $SANDBOX && cd $SANDBOX && uv init && uv venv && uv pip install -e $PROJECT_ROOT
```

Verify:
- `cd $SANDBOX && source .venv/bin/activate && sss --help` prints usage
- `cd $SANDBOX && source .venv/bin/activate && sss --version` prints a version number

### Phase 1: Init (Scaffold)

```bash
cd $SANDBOX && source .venv/bin/activate && sss init my-test-app
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
cd $APP_DIR && source $SANDBOX/.venv/bin/activate && time sss deploy -y -v
```

**Check:**
- [ ] Progress output shows step numbers `[1/5]` through `[5/5]`
- [ ] Each step completes with green status
- [ ] Health check table prints with all resources showing Active/Ready/Deployed
- [ ] Memory resource appears in health check (Active status)
- [ ] "Deployed successfully!" message with a URL is printed
- [ ] Recovery commands are shown
- [ ] `curl <URL>` returns 200 with the frontend HTML
- [ ] `curl -X POST <URL>/api/invoke -H "Content-Type: application/json" -d '{"message":"hello"}'` returns a streaming agent response

**Record:** Total deploy time, any warnings, any confusing output.

**PAUSE — User Browser Test**: After completing automated checks, use `AskUserQuestion` to show the deployed frontend URL and ask the user to test it in their browser. Present the URL clearly and ask them to confirm the frontend loads and the chat works. Wait for user confirmation before proceeding.

### Phase 2a: MCP Tool Verification (online)

After a successful deploy, verify that the deployed agent can discover and invoke MCP tools. The agent's `.mcp.json` is deployed alongside the agent code, so tools should be auto-loaded at runtime.

1. Invoke the agent via the API with a prompt that requires MCP tool use (e.g. ask it to use one of its configured tools)
2. Check CloudWatch logs for the agent's Lambda function for evidence of MCP tool loading/invocation

**Check:**
- [ ] Agent responds successfully when prompted to use an MCP tool
- [ ] CloudWatch logs show MCP tool discovery or invocation entries

**Record:** Whether MCP tools loaded automatically, any errors in logs.

### Phase 2b: Conversation Memory Test

After first deploy, verify that AgentCore Memory preserves conversation history within a session. Generate a single `session_id` and send two sequential messages:

```bash
SESSION_ID=$(uuidgen)

# Message 1: Tell the agent something memorable
curl -s -X POST <URL>/api/invoke \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"My name is Alice and I like strawberries.\", \"session_id\":\"$SESSION_ID\"}"

# Message 2: Ask the agent to recall it
curl -s -X POST <URL>/api/invoke \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"What is my name and what do I like?\", \"session_id\":\"$SESSION_ID\"}"
```

**Check:**
- [ ] Message 2 response contains "Alice" — proves conversation history was retrieved
- [ ] Message 2 response contains "strawberries" — proves context persisted
- [ ] Using a *different* session_id does NOT recall the conversation (session isolation)

**Record:** Whether memory worked on first try, any latency between messages needed.

### Phase 3: Update & Redeploy — 2nd WoW (Fast Iteration)

Make a visible change to the frontend (e.g., update `<h1>` text in `$APP_DIR/app/index.html`), then:

```bash
cd $APP_DIR && source $SANDBOX/.venv/bin/activate && time sss deploy -y -v
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
cd $APP_DIR && source $SANDBOX/.venv/bin/activate && sss status
```

**Check:**
- [ ] Status table shows all resources including Memory
- [ ] URL is printed
- [ ] No error warnings

### Phase 5: Destroy — Clean Teardown

```bash
cd $APP_DIR && source $SANDBOX/.venv/bin/activate && sss destroy --yes
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
rm -rf $APP_DIR
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

## Conversation Memory (AgentCore Memory)

**Rating**: X/10

**Memory recall test:**
- Told agent: "My name is Alice and I like strawberries"
- Asked: "What is my name and what do I like?"
- Agent recalled correctly: YES/NO
- Session isolation verified: YES/NO

**Positives:**
- ...

**Issues:**
- ...

## MCP Tools (Online Verification)

**Rating**: X/10

- Agent auto-loaded MCP tools after deploy: YES/NO
- Agent responded to MCP tool prompt: YES/NO
- CloudWatch logs confirm tool discovery: YES/NO

**Positives:**
- ...

**Issues:**
- ...

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
