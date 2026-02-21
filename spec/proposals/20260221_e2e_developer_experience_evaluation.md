# Proposal: End-to-End Developer Experience Evaluation

**Date**: 2026-02-21
**Author**: Claude Agent (DevRel evaluation)
**Status**: Implemented (with findings)

## Background

This document records an honest end-to-end evaluation of three-stars from a developer's perspective. The goal was to follow the complete lifecycle: install → init → build → deploy → modify → redeploy, and compare the experience against existing CDK-based tooling.

## Test Environment

- Region: us-west-2
- Python: 3.12 (system), uv for package management
- AWS credentials: temporary session tokens

## Evaluation Timeline

### Phase 1: Install (`pip install three-stars` / `uv sync`)

**Result: PASS**
- `uv sync` installed everything cleanly in ~3 seconds
- `sss --help` worked immediately
- The `sss` alias is memorable and the full `three-stars` alias is also available

**DX Verdict**: Smooth. Comparable to or better than CDK which requires Node.js + npm + `cdk bootstrap`.

### Phase 2: Init (`sss init my-app`)

**Result: PASS with issues**
- Scaffolding worked instantly, output was clean and instructive
- Nice directory structure summary printed

**Issues Found**:
1. **Wrong default model ID**: Template uses `anthropic.claude-sonnet-4-20250514` but Bedrock requires either the versioned ID (`anthropic.claude-sonnet-4-20250514-v1:0`) or an inference profile (`us.anthropic.claude-sonnet-4-20250514-v1:0`). This means `python agent/agent.py "Hello"` fails out of the box.
2. **Missing `pip install` step**: README says "run `python agent/agent.py`" but doesn't mention installing `boto3` or the agent's dependencies first.

### Phase 3: Local Test

**Result: FAIL out of the box, PASS after fixes**
- `python agent/agent.py "What is Bedrock?"` → `ModuleNotFoundError: No module named 'boto3'`
- After installing boto3: `ValidationException: The provided model identifier is invalid`
- After fixing model ID to inference profile: works

**DX Verdict**: A developer following the README exactly would fail twice before getting a response.

### Phase 4: Deploy (`sss deploy`)

**Result: FAIL on first attempt, PASS after 3 bug fixes**

Three bugs prevented successful deployment:

#### Bug 1: CloudFront `create_distribution_with_tags` API misuse
- **File**: `three_stars/resources/cdn.py:251`
- **Issue**: Passed `DistributionConfig` and `Tags` as separate kwargs, but the API requires `DistributionConfigWithTags` as a wrapper
- **Fix**: Wrapped both into `DistributionConfigWithTags={...}`

#### Bug 2: AgentCore `update_agent_runtime` missing `networkConfiguration`
- **File**: `three_stars/resources/agentcore.py:309`
- **Issue**: The create path includes `networkConfiguration: {"networkMode": "PUBLIC"}` but the update path omits it. The API requires it in both.
- **Fix**: Added `"networkConfiguration": {"networkMode": "PUBLIC"}` to update kwargs

#### Bug 3: OAC creation not idempotent
- **File**: `three_stars/resources/cdn.py:118`
- **Issue**: If deployment fails after OAC creation but before CloudFront distribution, retrying fails with `OriginAccessControlAlreadyExists`
- **Fix**: Added try/except to look up existing OAC by name on conflict

After fixing all three bugs, deployment succeeded. Infrastructure provisioning (S3 + AgentCore + Lambda + Lambda@Edge + CloudFront) completed and the frontend was accessible at `https://dfi59srwj63su.cloudfront.net`.

**DX Verdict**: The "deploy with one command" promise is compelling, but the tool is not production-ready due to bugs in the deployment pipeline. The post-deployment health check table and CloudFront propagation messaging are well-designed UX touches.

### Phase 5: AgentCore Runtime Invocation

**Result: CRITICAL FAILURE**

The deployed frontend loads and serves HTML correctly. However, the AgentCore runtime consistently fails with:
```
RuntimeClientError: Runtime initialization time exceeded. Please make sure that initialization completes in 30s.
```

This happened with every variation tested:
1. Lambda-style handler (original template) — FAIL
2. Lazy-import handler (boto3 imported inside handler) — FAIL
3. BedrockAgentCoreApp SDK with `@app.entrypoint` — FAIL
4. Pure stdlib HTTP server with `/invocations` and `/ping` endpoints — FAIL
5. PYTHON_3_11 runtime — FAIL
6. PYTHON_3_13 runtime — FAIL
7. With requirements.txt — FAIL
8. Without requirements.txt — FAIL

**Root Cause Analysis**: The `codeConfiguration` deployment mode for AgentCore (uploading a zip to S3) appears to have a fundamental initialization issue. Even the simplest possible Python HTTP server (zero external dependencies, ~40 lines of code) exceeds the 30s initialization limit. This suggests the problem is in AgentCore's `codeConfiguration` runtime environment setup, not in the user's agent code.

**DX Verdict**: This is a showstopper. The entire value proposition of three-stars depends on AgentCore working end-to-end. The official AgentCore documentation recommends container-based deployment (via ECR + Docker), which three-stars does not support. The `codeConfiguration` mode appears to be either underdocumented, unreliable, or simply broken.

### Phase 6: Modify & Redeploy (Second WoW Point)

**Result: Partially tested**
- The update path for `sss deploy` works (re-uploads agent code, skips existing infrastructure)
- Redeployment is fast (seconds instead of minutes)
- However, we could not verify end-to-end because AgentCore invocation never succeeded

### Additional Issues Found

1. **Agent template uses wrong handler pattern**: The starter agent uses Lambda-style `handler(event, context)` — AgentCore requires either `@app.entrypoint` from the AgentCore SDK, or a server implementing `/invocations` and `/ping` HTTP endpoints
2. **Lambda timeout too short**: Default is 30s, but AgentCore cold start + Bedrock model invocation can exceed this
3. **No streaming support**: Agent responses use synchronous `invoke_model` — streaming would dramatically improve perceived latency

## Comparison: three-stars vs CDK

| Dimension | three-stars | CDK |
|-----------|-------------|-----|
| **Install** | `pip install three-stars` (seconds) | `npm install -g aws-cdk` + `cdk bootstrap` (minutes) |
| **Init** | `sss init` (instant) | `cdk init` (seconds) |
| **Config** | 1 YAML file (20 lines) | TypeScript/Python stacks (100+ lines) |
| **Deploy** | `sss deploy` (one command) | `cdk deploy` (one command) |
| **Learning curve** | Low (just YAML + agent code) | High (CloudFormation, constructs, stacks) |
| **Flexibility** | Low (fixed architecture) | High (any AWS resource) |
| **Debugging** | Poor (no stack traces, opaque errors) | Better (CloudFormation events) |
| **Rollback** | None (state file only) | Built-in (CloudFormation) |
| **Reliability** | 3 deployment bugs found | Mature, well-tested |
| **E2E working** | No (AgentCore fails) | Yes (proven stack) |

## What three-stars Does Well

1. **Concept is compelling**: "Three stars" metaphor (Backend + CDN + API Bridge) is clear and marketable
2. **CLI UX is polished**: Rich terminal output, spinner animations, health check tables
3. **Config is minimal**: 20-line YAML vs 100+ lines of CDK
4. **Update path works**: Incremental deploys are fast
5. **Architecture is sound**: No CDK/CloudFormation = faster, more transparent deployments

## What Needs Fixing Before Launch

### P0 (Blocking)
1. **AgentCore runtime invocation fails**: The `codeConfiguration` deployment mode does not work. Consider switching to container-based deployment (ECR)
2. **Agent template uses wrong handler pattern**: Must use `@app.entrypoint` from `bedrock-agentcore` SDK, not Lambda-style handlers
3. **CloudFront `create_distribution_with_tags` bug**: Must use `DistributionConfigWithTags` wrapper
4. **AgentCore update missing `networkConfiguration`**: Required parameter omitted in update path

### P1 (Important)
5. **Default model ID is invalid**: Use inference profile format `us.anthropic.claude-sonnet-4-20250514-v1:0`
6. **OAC creation not idempotent**: Retry after partial failure breaks
7. **Lambda timeout too short**: 30s is insufficient for AgentCore + Bedrock chain, should be 120s+
8. **No streaming support**: Critical for AI chat UX

### P2 (Nice to have)
9. **Missing local test instructions**: README should mention `pip install -r agent/requirements.txt`
10. **Python 3.13 runtime**: Default should be PYTHON_3_13 (recommended by AWS)
11. **`sss status` should show AgentCore invocation health**: Currently only checks resource existence, not functionality

## Honest Assessment

**Is three-stars differentiated from CDK?** Yes, in concept. The simplicity of "one YAML file + one command" is genuinely attractive compared to CDK's learning curve. A developer who wants to deploy an AI chat app shouldn't need to understand CloudFormation constructs.

**Is it ready for developers?** No. The tool has critical bugs that prevent the core use case from working. A developer following the README will:
1. Fail at local testing (wrong model ID)
2. Fail at deployment (3 bugs)
3. Fail at runtime (AgentCore `codeConfiguration` doesn't work)

**What's the path forward?**
1. Fix the 4 P0 bugs (especially AgentCore container deployment)
2. Test the complete flow end-to-end with real AWS accounts
3. Add integration tests that deploy and invoke, not just mock
4. Consider whether `codeConfiguration` is viable or if container deployment is needed

## Implementation Plan

The following fixes have been applied in this evaluation:
- [x] Fix CloudFront `create_distribution_with_tags` API call
- [x] Fix AgentCore `update_agent_runtime` missing `networkConfiguration`
- [x] Fix OAC creation idempotency
- [x] Update default model ID to inference profile format
- [x] Update starter template to use `@app.entrypoint` from AgentCore SDK
- [x] Update default runtime to PYTHON_3_13
- [ ] Switch to container-based AgentCore deployment (needs further investigation)
- [ ] Add streaming support
- [ ] Increase default Lambda timeout
