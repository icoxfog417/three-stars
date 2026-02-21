# Proposal: AWS Developer Experience End-to-End Review

**Date**: 2026-02-21
**Author**: AWS Developer Experience Lead
**Status**: Proposed

## Executive Summary

This document provides an end-to-end developer experience (DX) review of the `three-stars` CLI tool, evaluating the journey from README to deployment. The review assesses whether the project's "3 stars" brand promise — simplicity through exactly three AWS resources — is established and maintained throughout the experience.

**Overall Assessment**: The tool has strong architectural foundations and clean code, but several DX gaps undermine the "3 stars" simplicity promise. The most critical issues are naming inconsistencies between documentation and implementation, and a mismatch between the "3 resources" brand and the actual 7-resource deployment.

---

## 1. README & First Impression

### What Works
- The opening line is clear: "Deploy AI-powered web applications to AWS with a single command."
- The Quick Start section has exactly the right structure: Install → Create → Deploy → Done.
- The architecture ASCII diagram is helpful and scannable.
- The `three-stars.yml` config example is minimal and well-commented.

### Issues Found

#### CRITICAL: CLI Command Name Mismatch
The README documents `three-stars` as the CLI command throughout:
```bash
three-stars init my-app    # README says this
three-stars deploy         # README says this
```
But the actual entry point in `pyproject.toml` is `sss`:
```toml
[project.scripts]
sss = "three_stars.cli:main"
```
Running `three-stars init` will fail with "command not found." This is the most damaging DX issue — the very first command a user tries will fail.

**Recommendation**: Either rename the entry point to `three-stars` to match the README, or update the README to use `sss`. The `sss` name has discoverability problems (a developer seeing `sss deploy` in a colleague's terminal wouldn't know what tool it is), so `three-stars` is the better choice. If brevity is desired, provide both entry points.

#### CRITICAL: Python Version Contradiction
- README Prerequisites says "Python 3.11+"
- `pyproject.toml` requires `>=3.12`
- `.python-version` says `3.12`
- AgentCore runtime is `PYTHON_3_11`

A developer with Python 3.11 will read "Python 3.11+" in the README, run `pip install three-stars`, and get: `ERROR: Package 'three-stars' requires a different Python: 3.11.x not in '>=3.12'`. This is a trust-breaking moment.

**Recommendation**: Either lower `requires-python` to `>=3.11` (the code uses `from __future__ import annotations` and no 3.12-only features) or update the README to say "Python 3.12+".

#### MODERATE: The "3 Stars" Are Not Actually 3 Resources
The README promises 3 resources:
1. Amazon Bedrock AgentCore
2. Amazon CloudFront + S3
3. CloudFront Functions

But the actual deployment creates 7+ resources:
1. S3 Bucket
2. IAM Role (AgentCore)
3. AgentCore Runtime + Endpoint
4. IAM Role (Lambda bridge)
5. Lambda Function (API bridge) + Function URL
6. IAM Role (Lambda@Edge)
7. Lambda@Edge Function
8. CloudFront Distribution + 2 OACs

Moreover, "CloudFront Functions" (item 3 in the README) is not used at all. The implementation uses Lambda + Lambda@Edge instead. The README architecture diagram is factually incorrect.

**Recommendation**: Reframe the "3 stars" as conceptual layers rather than literal AWS resources:
1. **AI Backend** (AgentCore) — runs your agent
2. **Frontend CDN** (CloudFront + S3) — serves your app
3. **API Bridge** (Lambda) — connects them

This preserves the brand while being truthful about what's deployed. Update the architecture diagram to reflect Lambda (not CloudFront Functions).

---

## 2. Install Experience

### What Works
- `pip install three-stars` is the correct packaging model for a Python CLI tool.
- Dependencies are minimal and well-chosen: boto3, click, rich, pyyaml.
- `uv sync` works cleanly with the lock file.

### Issues Found

#### MODERATE: No `uv` Installation Path Documented
The project includes a `uv.lock` file and `.python-version`, indicating `uv` is the intended workflow. But the README only mentions `pip`. Given that `uv` manages the Python version automatically (avoiding the 3.11/3.12 confusion), it should be the primary documented path.

**Recommendation**: Add `uv` as the first installation option:
```bash
# With uv (recommended)
uv tool install three-stars

# With pip
pip install three-stars
```

#### MINOR: Dev Installation Section Could Be Clearer
The dev setup section says `pip install -e ".[dev]"` but dev dependencies are under `[dependency-groups]`, not `[project.optional-dependencies]`. This means `pip install -e ".[dev]"` doesn't actually install dev deps on older pip versions. Only `uv sync` or pip 24.1+ with `--group dev` works.

**Recommendation**: Document the canonical dev setup as `uv sync` and note pip compatibility.

---

## 3. CLI Experience for Building an Agent

### What Works
- `sss init my-app` scaffolds a clean, minimal project structure.
- The scaffolded `agent.py` is a working, runnable handler with Bedrock integration.
- The `index.html` template is a functional chat UI — not a placeholder.
- `three-stars.yml` has sensible defaults with inline comments.
- The "Next steps" output after init is helpful:
  ```
  Next steps:
    cd test-app
    # Edit agent/agent.py with your agent logic
    # Edit app/index.html with your frontend
    sss deploy
  ```

### Issues Found

#### MODERATE: No Local Development / Testing Story
After `sss init`, there is no way to test the agent locally before deploying to AWS. The developer's only option is blind deployment. Most comparable tools (AWS SAM, Amplify, Vercel) provide a local dev server.

**Recommendation**: Add `sss dev` or document a local testing approach:
```bash
# Run agent locally
python agent/agent.py  # should work with a simple test
```
At minimum, the scaffolded `agent.py` should include a `if __name__ == "__main__":` block for local testing.

#### MINOR: Template Project Name Substitution Is Fragile
`init.py:42` does `content.replace("my-ai-app", name)` — a global string replace on the YAML file. If the template contains "my-ai-app" in multiple places (e.g., a description field), all will be replaced. This is fine today but brittle for future templates.

#### MINOR: Agent Template Imports `boto3` Unconditionally
The starter `agent.py` imports `boto3` at module level. If a developer wants to test locally without AWS credentials, the import will fail. Consider lazy importing or documenting the local testing story.

---

## 4. Deploy & Update Experience

### What Works
- Step-numbered progress with elapsed time (`[1/5] S3 storage ready`) is excellent.
- Post-deployment health check table provides immediate confidence.
- State backup before deploy (`.three-stars-state.json.bak`) is a good safety net.
- Recovery guidance on failure is actionable:
  ```
  State has been saved. To recover:
    Check status:  sss status
    Retry deploy:  sss deploy
    Clean up:      sss destroy
  ```
- Idempotent resource creation handles "deploy again" without errors.
- `--force` flag for clean-slate recovery is well-designed.
- Agent code updates on redeploy (AgentCore runtime update) work correctly.

### Issues Found

#### MODERATE: Deploy Step Order Differs from README
The README shows this deploy output:
```
✓ IAM role ready
✓ S3 bucket ready
✓ Uploaded 2 files
✓ Agent packaged
✓ AgentCore runtime active
✓ CloudFront Function ready
✓ CloudFront distribution created
```
But the actual deploy steps are:
```
[1/5] S3 storage ready
[2/5] AgentCore ready
[3/5] Lambda API bridge ready
[4/5] Lambda@Edge function ready
[5/5] CloudFront distribution created
```
The README output mentions "CloudFront Function" (which doesn't exist) and omits Lambda bridge/Lambda@Edge. The mismatch erodes trust in the documentation.

**Recommendation**: Update the README deploy output to match the actual implementation.

#### MODERATE: No Frontend-Only Update Path
When a developer only changes `index.html`, `sss deploy` still runs all 5 steps including AgentCore update, Lambda bridge, and Lambda@Edge checks. This is slower than necessary. A `sss deploy --app-only` flag or automatic detection of what changed would improve the iteration loop.

#### MINOR: CloudFront Propagation Messaging
The tool correctly reports "propagation ~5-10 min" but doesn't offer `sss status` polling. A `sss deploy --wait` flag that polls until CloudFront reaches "Deployed" would close the loop.

#### MINOR: `--verbose` Flag Does Very Little
The `--verbose` flag only adds one extra line when updating AgentCore (`Updating AgentCore runtime {id}...`). For a deploy that takes several minutes, verbose mode should show more detail (e.g., IAM role creation, S3 upload counts, Lambda code size).

---

## 5. Status & Destroy Experience

### What Works
- `sss status` queries live AWS state, not just local file.
- Destroy shows resources to be deleted before confirming — good UX.
- Reverse-order teardown is correct (CDN first, S3 last).
- `--yes` flag for scripted teardown.
- Graceful handling of missing resources during destroy.

### Issues Found

#### MINOR: Status Doesn't Show URL Prominently
`sss status` buries the URL at the bottom. It should be the first thing shown after the project name, since checking "what's my URL?" is the most common reason to run status.

#### MINOR: No Cost Indicator
After deployment, developers want to know "how much will this cost me?" A rough cost estimate (even just a link to the AWS pricing calculator) would reduce anxiety about leaving resources running.

---

## 6. Code Quality Assessment

### Strengths
- **59 tests, all passing** with zero lint warnings. The test suite is thorough.
- **Typed state management** with dataclasses is well-designed. The typed `DeploymentState` with per-resource state classes prevents a whole class of dictionary key errors.
- **Resource modules are decoupled** — they never import each other. Cross-resource wiring is exclusively in the orchestrator. This is a clean architectural pattern.
- **Frozen `ResourceNames` dataclass** as single source of truth for naming is a good design.
- **Idempotent operations** throughout — all `create` operations handle "already exists" gracefully.
- **Security**: S3 public access is blocked, Lambda function URLs use `AWS_IAM` auth (not `NONE`), IAM roles follow least-privilege.

### Code Issues

#### IAM Role Sleep Hardcoded
Three places (`agentcore.py:247`, `api_bridge.py:221`, `edge.py:155`) have `time.sleep(10)` after IAM role creation. This is a known AWS issue (IAM propagation delay) but a fixed 10-second sleep is fragile. Consider an exponential backoff retry on the downstream operation instead.

#### Duplicate `_wait_for_lambda_active` Functions
`api_bridge.py` and `edge.py` each have their own copy of `_wait_for_lambda_active()`. This should be in `_base.py` or a shared utility.

#### CDN Module Has Cross-Module Imports
`cdn.py:67-73` imports from `storage` and `api_bridge` inside the `deploy()` function:
```python
from three_stars.resources.storage import set_bucket_policy_for_cloudfront
from three_stars.resources.api_bridge import grant_cloudfront_access
```
This violates the documented design principle that "modules never import each other." These operations should be called by the orchestrator (`deploy.py`) instead.

---

## 7. The "3 Stars" Assessment

The project name and brand promise "three stars" — three AWS resources, three steps, three things to think about. Let me assess whether this promise is delivered:

### Star 1: AI Backend (AgentCore) — ESTABLISHED
The agent development experience is clear. Write a `handler()` function, deploy, done. The Bedrock integration is transparent — the developer doesn't need to think about model endpoints or auth. The starter template works out of the box.

### Star 2: Frontend CDN (CloudFront + S3) — ESTABLISHED
Drop HTML/CSS/JS into `app/`, deploy, get a CDN URL. This works exactly as expected. The S3 + CloudFront + OAC setup is correctly locked down. The developer doesn't think about infrastructure.

### Star 3: API Bridge (Connecting Them) — PARTIALLY ESTABLISHED
The bridge exists and works, but it's not as transparent as Stars 1 and 2:
- The developer doesn't control or configure the Lambda bridge code.
- The README claims "CloudFront Functions" but the implementation uses Lambda, creating confusion.
- The architecture has become complex (Lambda + Lambda@Edge + OAC signing) — this is necessary for security, but the "simplicity" promise is undermined because the developer sees 5 deployment steps, not 3.

### Summary: 2.5 out of 3 Stars Established

The core developer journey works, but the brand is undermined by:
1. Documentation lying about the architecture (CloudFront Functions vs Lambda)
2. CLI name mismatch (`three-stars` vs `sss`)
3. Python version contradiction
4. 5 deployment steps for a "3 resource" tool

---

## 8. Prioritized Recommendations

### P0 — Fix Before Any Public Use
1. **Fix CLI entry point**: Either rename `sss` to `three-stars` or update all documentation
2. **Fix Python version**: Align README, pyproject.toml, and .python-version
3. **Fix architecture description**: Replace "CloudFront Functions" with Lambda in README

### P1 — Fix for Good DX
4. **Reframe the "3 stars" brand**: Conceptual layers (backend, frontend, bridge) not literal resource count
5. **Update README deploy output**: Match actual 5-step progress output
6. **Add local testing story**: `if __name__ == "__main__"` in agent template at minimum

### P2 — Polish
7. **Document `uv` installation**: Primary path alongside pip
8. **Add `--app-only` deploy**: Faster iteration on frontend changes
9. **Expand `--verbose` output**: Make it useful
10. **Move cross-module imports**: CDN module shouldn't import storage/api_bridge
11. **Deduplicate `_wait_for_lambda_active`**: Shared utility in `_base.py`

---

## Impact

- **Requirements**: Update user persona to reflect the actual 5-step deploy and Lambda architecture
- **Design**: Update architecture diagram in Section 1.1 to reflect Lambda bridge instead of CloudFront Functions
- **Tasks**: New sprint for P0 and P1 fixes

## Alternatives Considered

- **Keep `sss` as CLI name**: Rejected. Discoverability is too poor. Nobody seeing `sss` in a terminal will know what it does.
- **Actually use CloudFront Functions**: CloudFront Functions can't make network calls to AgentCore, so Lambda is the correct choice. The documentation needs to catch up.
- **Reduce to literally 3 resources**: Not feasible. IAM roles, Lambda@Edge for OAC signing, and the Lambda bridge are architecturally necessary. The brand should be about the developer mental model, not the AWS resource count.
