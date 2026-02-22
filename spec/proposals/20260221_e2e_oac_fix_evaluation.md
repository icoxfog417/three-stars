# Proposal: E2E Evaluation — CloudFront OAC Fix and Updated DX Assessment

**Date**: 2026-02-21
**Author**: Claude Agent (DevRel evaluation, session 2)
**Status**: Implemented

## Background

This document records a second end-to-end evaluation of three-stars, building on fixes from the first evaluation (PR #9). The first session identified and fixed AgentCore deployment issues. This session focused on verifying the full request path: **Browser → CloudFront → Lambda → AgentCore → Bedrock → response**.

## Test Environment

- Region: us-east-1
- Python: 3.12 (via uv)
- AWS Account: 872515288562 (temporary credentials)
- Project name: `oac-test`

## Findings

### What Worked

1. **Install**: `uv sync` — clean, fast
2. **Init**: `sss init oac-test` — clean scaffold, good output
3. **Deploy**: `sss deploy` — all infrastructure created successfully
4. **Frontend**: CloudFront → S3 served the React chat UI correctly
5. **AgentCore**: Direct Lambda invocation reached Bedrock and returned AI responses
6. **Redeploy**: `sss deploy` update path completed in ~28 seconds

### What Failed: CloudFront → Lambda API Path (403)

The critical failure was that **all API requests through CloudFront returned HTTP 403**:

```
{"Message":"User: anonymous is not authorized to perform:
lambda:InvokeFunctionUrl ... because no resource-based policy allows the
lambda:InvokeFunctionUrl action"}
```

This meant the deployed application was non-functional — the frontend loaded but could not communicate with the AI backend.

### Root Cause Analysis (Hypothesis-Driven)

We used a hypothesis-driven approach to isolate two independent root causes:

#### Root Cause 1: Host Header Forwarding Breaks SigV4

**File**: `three_stars/resources/cdn.py`

The API cache behavior used legacy `ForwardedValues` with `Headers: ["*"]`, which forwards the CloudFront domain as the `Host` header to the Lambda function URL origin. CloudFront OAC uses SigV4 signing, and when the Host header doesn't match the Lambda function URL's actual domain, the signature verification fails.

**Fix**: Replaced `ForwardedValues` with managed cache policies:
- `CachingDisabled` (ID: `4135ea2d-6df8-44a3-9df3-4b5a84be39ad`)
- `AllViewerExceptHostHeader` (ID: `b689b0a8-53d0-40ab-baf2-68738e2966ac`)

These are the AWS-recommended policies for Lambda function URL origins with OAC.

**Reference**: https://dev.classmethod.jp/articles/cloudfront-lambda-url-sigv4-signer/

#### Root Cause 2: Missing `lambda:InvokeFunction` Permission

**File**: `three_stars/resources/api_bridge.py`

The code only granted `lambda:InvokeFunctionUrl`. Since 2024, AWS accounts block public access to Lambda function URLs by default. In this configuration, CloudFront OAC also requires `lambda:InvokeFunction` permission — otherwise OAC gets a 403 even with valid SigV4 signatures.

**Fix**: `grant_cloudfront_access()` now adds both permissions:
- `lambda:InvokeFunctionUrl` (for OAC signing)
- `lambda:InvokeFunction` (for accounts with public access block)

**Reference**: https://dev.classmethod.jp/articles/cloudfront-lambda-url-with-post-put-request/

### Additional Bugs Fixed

#### Bug 3: AgentCore Missing Environment Variables

**File**: `three_stars/resources/agentcore.py`

Agent code running on AgentCore had no `AWS_DEFAULT_REGION` environment variable. The starter template's `boto3.client("bedrock-runtime")` call failed with "You must specify a region" because AgentCore runtime doesn't inherit region from the instance metadata the way Lambda does.

**Fix**: Pass `environmentVariables: {"AWS_DEFAULT_REGION": config.region}` in both `create_agent_runtime` and `update_agent_runtime` calls.

#### Bug 4: Foundation Model IAM ARN Mismatch

**File**: `three_stars/resources/agentcore.py`

The IAM policy used `arn:aws:bedrock:*:{account_id}:*` which doesn't match foundation model ARNs. Foundation models have an empty account ID: `arn:aws:bedrock:us-east-1::foundation-model/...`.

**Fix**: Added `"arn:aws:bedrock:*::foundation-model/*"` as an additional resource in the IAM policy.

### DX Issues Observed (Not Fixed)

#### `--force` Flag Orphans Resources

When `sss deploy --force` is used and deployment fails partway, the local state file is already cleared. This means `sss destroy` can't find the orphaned cloud resources (CloudFront distributions, Lambda@Edge functions, OACs, etc.). The developer must manually clean up via AWS console or CLI.

This is a significant DX problem: the flag meant to "fix things" can make them worse.

#### No CloudFront Cache Invalidation on Redeploy

After frontend updates, CloudFront continues serving stale content from cache. There's no automatic invalidation on `sss deploy`. Developers would need to wait up to 24 hours (the default TTL) or manually invalidate.

#### AgentCore Code Updates Not Immediate

After `update_agent_runtime`, the new code doesn't appear to take effect immediately. In some cases a full destroy + recreate was required. The update API returns success, but the running runtime continues to use old code.

## Updated Comparison: three-stars vs CDK

| Dimension | three-stars (post-fixes) | CDK |
|-----------|------------------------|-----|
| **Install** | `pip install` / `uv sync` (seconds) | `npm install -g aws-cdk` + `cdk bootstrap` (minutes) |
| **Init → Deploy** | 2 commands, ~3 min | 2 commands + bootstrap, ~5 min |
| **Config complexity** | 20-line YAML | 100+ lines TypeScript/Python |
| **Learning curve** | Low | High |
| **E2E working** | **Yes** (after 4 bug fixes) | Yes (mature) |
| **Error recovery** | Poor (orphaned resources) | Good (CloudFormation rollback) |
| **Cache invalidation** | None | Configurable |
| **Flexibility** | Fixed architecture | Any AWS resource |

## Honest Assessment

**Is the value proposition real?** Yes. Deploying an AI chat app with `sss init` + `sss deploy` in under 3 minutes is genuinely compelling compared to CDK's learning curve. The "three stars" architecture (Backend + CDN + API Bridge) is sound and well-abstracted.

**Is it ready?** Not yet. The 4 bugs fixed in this session are all in core deployment logic — they break every deployment, not edge cases. However, all 4 fixes are straightforward (this commit). After these fixes, the full path works: browser → CloudFront → Lambda → AgentCore → Bedrock → response → UI.

**What remains for production readiness?**

### P0 (Blocking)
1. ~~CloudFront OAC 403~~ → **Fixed** (managed policies + dual permissions)
2. ~~AgentCore env vars~~ → **Fixed** (environmentVariables parameter)
3. ~~Foundation model IAM~~ → **Fixed** (wildcard resource for empty account ID)
4. `--force` flag orphans resources → Not yet fixed

### P1 (Important)
5. CloudFront cache invalidation on redeploy
6. AgentCore code update propagation reliability
7. Better error messages (currently opaque 403s with no guidance)

### P2 (Polish)
8. Streaming support for AI responses
9. `sss status` should verify end-to-end health, not just resource existence
10. Integration tests that deploy and invoke

## Implementation Plan

All 4 critical bugs have been fixed and committed:
- [x] CloudFront OAC: managed cache policies (`cdn.py`)
- [x] Lambda dual permissions (`api_bridge.py`)
- [x] AgentCore environment variables (`agentcore.py`)
- [x] Foundation model IAM ARNs (`agentcore.py`)
- [x] Template region_name fix (`templates/starter/agent/agent.py`)
- [x] Test updated for dual permissions (`tests/resources/test_api_bridge.py`)
- [x] All 64 tests pass

## Remaining Cleanup

AWS credentials expired before full cleanup completed. The following resources may remain in account 872515288562:
- IAM roles: `sss-oac-test-role`, `sss-oac-test-lambda-role`
- Orphaned from earlier deploy: CloudFront `EARJTWQ0ZHGKU` (disabled), Lambda@Edge `sss-e2e-test-app-edge-sha256`, OACs for `e2e-test-app`
