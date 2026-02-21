# Security Proposal vs. Current Code — Reflection Review

**Date**: 2026-02-21
**Author**: Claude Agent
**Status**: Review Complete

## Purpose

This document cross-references each finding in
`spec/proposals/20260221_security_code_review.md` and the approved countermeasure in
`spec/proposals/20260221_lambda_url_auth_countermeasures.md` against the current
codebase to determine which issues have been addressed, which remain open, and
which findings in the proposal are now outdated due to the module redesign.

---

## File Mapping (Proposal → Current Code)

The security proposal references the pre-redesign filenames. The Sprint 5 module
redesign renamed all resource modules:

| Proposal Reference | Current File |
|---|---|
| `lambda_bridge.py` | `three_stars/resources/api_bridge.py` |
| `cloudfront.py` | `three_stars/resources/cdn.py` |
| `s3.py` | `three_stars/resources/storage.py` |
| `destroy.py` | `three_stars/destroy.py` |
| `agentcore.py` | `three_stars/resources/agentcore.py` |
| `conftest.py` | `tests/conftest.py` |
| *(new)* | `three_stars/resources/edge.py` |

---

## Critical Findings

### Finding #1: Unauthenticated Lambda Function URL — FIXED

**Proposal**: `AuthType="NONE"` + `Principal="*"` at `lambda_bridge.py:249-261`.

**Current code** (`api_bridge.py:314-318`):
```python
resp = lam.create_function_url_config(
    FunctionName=function_name,
    AuthType="AWS_IAM",
)
```

The `_ensure_function_url()` function now uses `AuthType="AWS_IAM"`. The old
`add_permission(Principal="*")` call has been removed entirely. Instead,
`grant_cloudfront_access()` (`api_bridge.py:126-144`) adds a scoped permission for
`cloudfront.amazonaws.com` conditioned on the distribution ARN:

```python
lam.add_permission(
    ...
    Principal="cloudfront.amazonaws.com",
    SourceArn=distribution_arn,
    FunctionUrlAuthType="AWS_IAM",
)
```

**Lambda@Edge for SHA256**: Implemented in the new `edge.py` module. The
Lambda@Edge function computes `x-amz-content-sha256` for POST/PUT bodies, matching
the approved Option 1a from the countermeasures proposal. The CloudFront distribution
(`cdn.py:200-210`) associates the edge function on `origin-request` with
`IncludeBody=True`.

**Verdict**: Fully addressed. The entire OAC + Lambda@Edge architecture from the
approved proposal is implemented.

---

### Finding #2: No Rate Limiting — PARTIALLY ADDRESSED

**Proposal**: No WAF on CloudFront, no `ReservedConcurrentExecutions` on Lambda,
CloudFront API cache TTL=0.

**Current code**:
- `cdn.py:195-198`: API cache behavior still has `MinTTL=0`, `DefaultTTL=0`, `MaxTTL=0`.
  Every request hits Lambda.
- `api_bridge.py`: `put_function_concurrency(ReservedConcurrentExecutions=10)` now
  caps concurrent Lambda executions (see Finding #6).
- `cdn.py:130-263`: No `WebACLId` in the distribution config (no WAF).

**Verdict**: Lambda concurrency limit acts as a cost cap (Finding #6). WAF not
added — cost/complexity not justified for current threat model.

---

### Finding #3: Wildcard CORS — RESOLVED

**Proposal**: Remove CORS headers entirely from the Lambda handler.

The frontend and API share the same CloudFront domain, so all browser requests
are **same-origin** — CORS headers are never evaluated. Additionally, OAC blocks
direct Lambda invocation, so cross-origin callers cannot reach the endpoint at
all.

**Resolution**: Removed all three `Access-Control-*` headers from
`_BRIDGE_FUNCTION_CODE` in `api_bridge.py`. Only `Content-Type` remains.

---

## High Findings

### Finding #4: `destroy` Deletes State on Partial Failure — RESOLVED

**Proposal**: `destroy.py` wipes the state file unconditionally, even when
some resources fail to delete.

**Resolution**: Each successful resource deletion now nulls that field on the
state object. On partial failure, `save_state()` writes the reduced state
(containing only the failed resources) so the user can retry. `delete_state()`
is only called when all resources are successfully deleted.

---

### Finding #5: No Tests for deploy/destroy/cloudfront/lambda_bridge — PARTIALLY FIXED

**Proposal**: Zero tests for `deploy.py`, `destroy.py`, `cloudfront.py`,
`lambda_bridge.py`, `cf_function.py`, `status.py`.

**Current state**:

| Module | Proposal Status | Current Status |
|--------|----------------|----------------|
| `api_bridge.py` (was `lambda_bridge.py`) | Zero tests | **261 lines of tests** — covers function creation, IAM auth on URL, idempotency, CloudFront permission grant, role CRUD |
| `cdn.py` (was `cloudfront.py`) | Zero tests | **110 lines of tests** — covers OAC creation (S3 and Lambda types), distribution with Lambda origin, OAC deletion |
| `edge.py` (new) | N/A | **Tested via `test_api_bridge.py`** — edge role and function creation/deletion tested |
| `deploy.py` | Zero tests | **Still zero direct tests** |
| `destroy.py` | Zero tests | **Still zero direct tests** |
| `status.py` | Zero tests | **Still zero direct tests** |
| `cf_function.py` | Zero tests | **Deleted** (replaced by Lambda@Edge) |

**Key test for security finding**: `test_api_bridge.py:63-78` explicitly asserts
that `AuthType == "AWS_IAM"` on the function URL — this is a regression test for
Finding #1.

**Verdict**: Partially addressed. Resource modules now have tests, but the
orchestrators (`deploy.py`, `destroy.py`, `status.py`) remain untested. No
integration test for the full lifecycle.

---

### Finding #6: No Lambda Concurrency Limit — RESOLVED

**Proposal**: No `ReservedConcurrentExecutions` on Lambda, leading to unbounded
Bedrock model invocation costs.

**Resolution**: Added `put_function_concurrency(ReservedConcurrentExecutions=10)`
in `_create_lambda_function()` after function creation/update. This caps
concurrent Lambda executions at 10, bounding Bedrock invocation costs.

---

## Medium Findings

### Finding #7: No WAF on CloudFront — OPEN

**Current code** (`cdn.py:232-241`): The `dist_config` dict has no `WebACLId` field.

**Verdict**: Not addressed.

---

### Finding #8: No Resource Tagging — FIXED

**Proposal**: No tags on any AWS resources.

**Current code**: All resource modules accept `tags` parameters and apply them:
- `storage.py:34-35`: Calls `_tag_bucket()` with `put_bucket_tagging()`.
- `agentcore.py:46,209-218`: Tags on IAM role creation.
- `api_bridge.py:78,182-183,248-249`: Tags on IAM role and Lambda function.
- `edge.py:53-55,121-122,185`: Tags on IAM role and Lambda@Edge function.
- `cdn.py:249-254`: Tags on CloudFront distribution via `create_distribution_with_tags()`.

Standard tags are computed by `config.py:183-195` (`get_resource_tags()`) and include
`three-stars:project`, `three-stars:managed-by`, and `three-stars:region`. User-defined
tags from `three-stars.yml` are merged in.

**Verdict**: Fully addressed.

---

### Finding #9: IAM Role Policy Not Updated on Re-deploy — RESOLVED

**Proposal**: When a role already exists, `create_iam_role` returns early without
updating the inline policy.

**Resolution**: Removed early return from the `EntityAlreadyExists` branch in all
three role creation functions (`api_bridge.py`, `agentcore.py`, `edge.py`).
`put_role_policy()` now runs unconditionally. The `time.sleep(10)` for IAM
propagation only runs when a new role is created (tracked via `created` flag).

---

### Finding #10: No Input Validation in Lambda Handler — OPEN

**Proposal**: Lambda handler passes raw HTTP body to `invoke_agent_runtime`
without any validation.

**Current code** (`api_bridge.py:23-56`): The embedded Lambda handler code still
passes the raw body directly:

```python
body = event.get("body", "{}")
...
resp = client.invoke_agent_runtime(
    agentRuntimeArn=runtime_arn,
    payload=body.encode("utf-8") if isinstance(body, str) else body,
    ...
)
```

No payload size check, no schema validation, no content-type verification.

**Verdict**: Not addressed.

---

## Low Findings

### Finding #11: `callable` (lowercase) instead of `Callable` — RESOLVED (N/A)

**Proposal**: `callable` used at `s3.py:81`.

The file `s3.py` has been replaced by `storage.py` during the module redesign.
A grep for `callable` across all Python files returns zero matches.

**Verdict**: No longer present. Resolved during module redesign.

---

### Finding #12: Untyped boto3 Client Params — OPEN

**Proposal**: ~8 private functions have untyped boto3 client parameters.

**Current code**: Multiple private functions still accept bare `lam` or `client`
parameters without type annotations:

- `api_bridge.py:276`: `_wait_for_lambda_active(lam, ...)`
- `edge.py:204`: `_wait_for_lambda_active(lam, ...)`
- `agentcore.py:349`: `_wait_for_runtime_ready(client, ...)`
- `agentcore.py:374`: `_wait_for_endpoint_ready(client, ...)`

**Verdict**: Not addressed. These remain untyped.

---

### Finding #13: No mypy/pyright Configured — OPEN

**Proposal**: No static type checker in `pyproject.toml`.

**Current code** (`pyproject.toml`): Only `ruff` is configured. No `[tool.mypy]`
or `[tool.pyright]` section. Neither `mypy` nor `pyright` is in the dev
dependencies.

**Verdict**: Not addressed.

---

### Finding #14: `AWS_DEFAULT_REGION` Not Cleaned Up in Test Teardown — OPEN

**Proposal**: `conftest.py:20-26` teardown does not clean up `AWS_DEFAULT_REGION`.

**Current code** (`tests/conftest.py:19-26`):
```python
yield
for key in [
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SECURITY_TOKEN",
    "AWS_SESSION_TOKEN",
]:
    os.environ.pop(key, None)
```

`AWS_DEFAULT_REGION` is set at line 18 but not removed in teardown.

**Verdict**: Not addressed.

---

## Summary

### Addressed (7 of 14)

| # | Finding | Resolution |
|---|---------|------------|
| 1 | Unauthenticated Lambda Function URL | **Fixed** — `AuthType=AWS_IAM` + CloudFront OAC + Lambda@Edge SHA256 |
| 3 | Wildcard CORS | **Resolved** — removed CORS headers; same-origin + OAC makes them unnecessary |
| 4 | `destroy` deletes state on partial failure | **Fixed** — state preserved with only failed resources on partial failure |
| 6 | No Lambda concurrency limit | **Fixed** — `ReservedConcurrentExecutions=10` via `put_function_concurrency` |
| 8 | No resource tagging | **Fixed** — all resources tagged with standard + custom tags |
| 9 | IAM role policy not updated on re-deploy | **Fixed** — `put_role_policy` now runs on every deploy, not just creation |
| 11 | `callable` vs `Callable` | **Resolved** — old file deleted during redesign |

### Partially Addressed (2 of 14)

| # | Finding | Status |
|---|---------|--------|
| 2 | No rate limiting | Lambda concurrency cap in place; WAF deferred (cost/complexity) |
| 5 | No tests for critical modules | Resource modules now have tests; orchestrators still have none |

### Not Addressed (5 of 14)

| # | Finding | Severity |
|---|---------|----------|
| 7 | No WAF on CloudFront | Medium |
| 10 | No input validation in Lambda handler | Medium |
| 12 | Untyped boto3 client params | Low |
| 13 | No mypy/pyright configured | Low |
| 14 | `AWS_DEFAULT_REGION` not cleaned in teardown | Low |

### Countermeasures Proposal Status

The approved Option 1a (OAC + Lambda@Edge) from
`20260221_lambda_url_auth_countermeasures.md` has been fully implemented:

| Implementation Step | Status | Location |
|---|---|---|
| Lambda@Edge function + IAM role | Done | `resources/edge.py` |
| Lambda Function URL `AuthType=AWS_IAM` | Done | `resources/api_bridge.py:314-317` |
| Remove `Principal=*` | Done | Old call removed entirely |
| CloudFront OAC for Lambda (`origin_type="lambda"`) | Done | `resources/cdn.py:47-49` |
| Lambda@Edge association on `/api/*` behavior | Done | `resources/cdn.py:200-210` |
| `grant_cloudfront_access()` with distribution ARN | Done | `resources/api_bridge.py:126-144`, called from `resources/cdn.py:72-74` |
| Deploy order updated (Lambda → Edge → CF → grant) | Done | `deploy.py:86-158` |
| Destroy order updated (CF → Edge → Lambda) | Done | `destroy.py:62-115` |
| State model with `EdgeState` + `lambda_oac_id` | Done | `state.py:39-53` |
