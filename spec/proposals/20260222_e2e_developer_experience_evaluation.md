# Proposal: E2E Developer Experience Evaluation Report

**Date**: 2026-02-22
**Author**: Claude Agent (DevRel E2E Test)
**Status**: Implemented

## Background

This document records an honest end-to-end developer experience evaluation of three-stars, testing the full workflow: install → init → deploy → iterate → redeploy. The evaluation assesses how a developer would feel using three-stars compared to existing CDK-based approaches.

## Test Environment

- **Region**: us-east-1
- **Account**: 872515288562 (Admin role)
- **Python**: 3.12.3
- **Platform**: Linux (sandbox)

## E2E Test Timeline

| Step | Command | Result | Notes |
|------|---------|--------|-------|
| Install | `uv sync` | OK (~6s) | Clean install, all deps resolved |
| Init | `sss init my-test-app` | OK (instant) | Clean project scaffold |
| First Deploy | `sss deploy --yes` | OK (~2min) | All 6 AWS resources created |
| Status Check | `sss status` | OK | Rich table, clear resource listing |
| Frontend Test | `curl https://...cloudfront.net/` | 200 OK | Static HTML served immediately |
| API Test (CloudFront) | `curl .../api/invoke` | 400 initially, then OK | Required investigation |
| API Test (Direct Lambda) | Lambda invoke | 200 OK | Bypassing CloudFront worked immediately |
| API Test (Direct AgentCore) | invoke_agent_runtime | 200 OK | Agent responds correctly |
| Template Update | Updated to Strands Agents | OK | Agent + requirements updated |
| Redeploy | `sss deploy --yes` | OK (~34s) | Update path detected automatically |
| Frontend Mod + Redeploy | Changed theme + tool | OK (~34s) | Both FE + BE updated |

## Honest Assessment: What Developers Would Feel

### First WoW Point: Initial Deploy

**Rating: 7/10 — Impressive but needs polish**

**Positives:**
- `sss init` → `sss deploy` is genuinely a 2-command deployment. Compared to CDK's `cdk init` → writing constructs → `cdk bootstrap` → `cdk deploy`, this is dramatically simpler.
- The Rich terminal output (tables, progress) feels professional.
- The 5-step deployment is clear and logical.
- State management is solid — `.three-stars-state.json` tracks everything.
- The `sss status` command gives a comprehensive overview.
- No CloudFormation stacks to manage, no template synthesis, no bootstrap buckets.

**Issues Found:**
1. **CloudFront → Lambda path had intermittent 400 errors**: The initial deploy showed "success" but the API didn't actually work via CloudFront. Direct Lambda invocation worked fine. This was eventually resolved but the root cause was non-obvious. A developer would be confused — "it says deployed but it doesn't work."
2. **No CloudFront cache invalidation on redeploy**: Frontend changes in S3 aren't visible because CloudFront serves stale cached content (24hr default TTL). A developer editing `index.html` and running `sss deploy` would see no change. This needs `CreateInvalidation` call after S3 upload.
3. **Health check doesn't verify the API path works**: The post-deploy health check shows resource status but doesn't actually test the `/api/invoke` endpoint. A smoke test would catch the 400 errors immediately.
4. **Deploy output is minimal**: When things go wrong, there's no `--debug` flag or detailed error trail. The `--verbose` flag didn't add much information.

### Second WoW Point: Iteration Speed

**Rating: 8/10 — This is where three-stars really shines**

**Positives:**
- **34-second redeploy** for both frontend and backend changes. This is the killer feature compared to CDK, where a `cdk deploy` with Lambda changes typically takes 60-120+ seconds due to CloudFormation changeset computation.
- The update path is automatically detected from existing state — no `--update` flag needed.
- AgentCore runtime update is smooth (packages agent, uploads to S3, updates runtime, waits for READY).
- The developer loop is tight: edit code → `sss deploy` → test.

**Issues Found:**
1. **CloudFront caching breaks frontend iteration**: As noted above, you can change `index.html` but won't see the change until the cache expires. This completely undermines the "fast iteration" promise for frontend changes.
2. **No `sss logs` command**: When the agent fails with a 400, developers need to manually check CloudWatch. A `sss logs` command showing recent agent/Lambda logs would save significant debugging time.

### Strands Agent Integration

**Rating: 5/10 — Needs significant work**

**Issues:**
1. **System prompt not honored**: The agent was configured with `system_prompt="You are Star Assistant..."` but still identified as "Claude". This suggests the Strands Agent initialization or system prompt isn't being properly passed through the AgentCore runtime.
2. **Custom tools not invoked**: The `@tool` decorated `current_time()` function was registered with the Agent but never called. The agent said "I don't have access to real-time information" instead of using the tool. This defeats the key value proposition of Strands Agents (tool use).
3. **Likely cause**: The zip-based deployment (via `uv pip install --python-platform aarch64-manylinux2014 --only-binary :all:`) may not correctly package all Strands dependencies for ARM64. Some native extensions (like `pydantic-core`) might fail or be missing. The agent falls back to raw Bedrock behavior without proper Strands initialization.
4. **No local testing path**: The template says `python agent/agent.py` starts a local server, but there's no documented way to test the agent locally before deploying. This makes debugging the tool/prompt issues very difficult.

### Comparison with CDK

| Aspect | three-stars | CDK |
|--------|------------|-----|
| Initial setup | `pip install three-stars` (1 command) | `npm install -g aws-cdk` + bootstrap + init |
| Project scaffold | `sss init` → 4 files | `cdk init` → 10+ files, complex structure |
| Deploy time (first) | ~2 min | 3-10 min (CloudFormation) |
| Deploy time (update) | ~34 sec | 60-120+ sec |
| Learning curve | Low (YAML config) | High (TypeScript/Python constructs, CF concepts) |
| Customization | Limited (fixed arch) | Unlimited (any AWS resource) |
| Debugging | Limited (no logs cmd) | CloudFormation events, CDK diff |
| Rollback | Manual (recovery commands) | CloudFormation automatic rollback |
| State management | JSON file (local) | CloudFormation (AWS-managed) |
| Multi-environment | Not supported | Native (stages, environments) |

### What Makes three-stars Differentiated

1. **Opinionated simplicity**: three-stars makes one architecture decision (S3 + CloudFront + Lambda + AgentCore) and optimizes for that. CDK gives you everything but makes you decide.
2. **Speed**: 34-second updates vs 60-120+ second CDK deploys. For AI app iteration, this matters.
3. **AI-first**: The starter template includes a working AI agent. CDK requires you to build everything from scratch.
4. **No CloudFormation**: Direct boto3 API calls mean faster operations and no changeset overhead.

### What CDK Still Does Better

1. **Reliability**: CloudFormation handles rollbacks, drift detection, and resource dependencies automatically. three-stars has no rollback mechanism.
2. **Multi-environment**: CDK stages and environment separation are mature. three-stars has no concept of dev/staging/prod.
3. **Extensibility**: CDK lets you add any AWS resource. three-stars is locked to its three-star architecture.
4. **Testing**: CDK has `cdk synth` + `cdk diff` for pre-deploy validation. three-stars goes straight to deploy.

## Critical Bugs Found

### P0 (Must Fix)
1. **Missing CloudFront cache invalidation**: Frontend changes don't appear after `sss deploy`. Add `CreateInvalidation` for `/*` after S3 upload.

### P1 (Should Fix)
2. **Strands tools not working in AgentCore**: The `@tool` decorator doesn't function in the zip deployment. Investigate dependency packaging for ARM64.
3. **System prompt not honored**: Strands Agent system prompt is ignored in AgentCore runtime.
4. **No post-deploy API smoke test**: Health check should verify `/api/invoke` actually works.

### P2 (Nice to Have)
5. **Add `sss logs` command**: Tail CloudWatch logs for Lambda bridge and AgentCore.
6. **Add `--debug` flag**: More verbose error output during deployment.
7. **Add local testing support**: `sss dev` or similar for local agent testing before deploy.

## Summary

three-stars delivers on its core promise of simplifying AI app deployment to AWS. The `sss init` → `sss deploy` flow is genuinely faster and simpler than CDK for the specific use case of deploying a Bedrock-powered AI web app. The 34-second iteration cycle is impressive.

However, the tool is not yet production-ready. The CloudFront caching bug means frontend iteration is broken, the Strands Agent integration (tools + system prompt) doesn't work in the deployed environment, and there's no debugging capability when things go wrong.

For a demo or prototype, three-stars would impress a developer. For real development work, the issues above would cause frustration within the first hour.

**Overall Rating: 6.5/10** — Strong concept, needs bug fixes and Strands integration work to deliver on the full promise.
