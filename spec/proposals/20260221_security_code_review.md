# Proposal: Security and Code Quality Review

**Date**: 2026-02-21
**Author**: Claude Agent
**Status**: Proposed

## Background

A comprehensive security and code quality review was conducted to evaluate the three-stars
codebase across six dimensions: secret leak risks, DDoS/cross-domain attack vectors, resource
management reliability, cost confidence, Python typing quality, and test quality.

## Findings

### 1. Secret Leak Analysis (AWS Credentials)

**Risk Level: LOW**

No hardcoded AWS credentials found. All AWS access delegates to the standard boto3 credential
chain. `.gitignore` properly excludes `.env`, `.env.*`, and `.three-stars-state.json`.

- `package_agent()` (`agentcore.py:35`) skips hidden files (`.env`) when building agent zips.
- Config loading uses `yaml.safe_load()` (`config.py:76`), preventing YAML deserialization attacks.
- State file stores resource ARNs/IDs but no credentials.

**Minor:**
- `conftest.py:20-26` teardown does not clean up `AWS_DEFAULT_REGION` env var.

---

### 2. DDoS and Cross-Domain Attack Vectors

**Risk Level: CRITICAL**

#### 2a. Unauthenticated Lambda Function URL (`lambda_bridge.py:249-261`)

`AuthType="NONE"` and `Principal="*"` make the Lambda publicly invocable without any
authentication. Anyone discovering the URL can bypass CloudFront and invoke Lambda directly.

**Recommendation:** Use `AuthType="AWS_IAM"` or add a shared secret header validated by Lambda.

#### 2b. Wildcard CORS (`lambda_bridge.py:53`)

`Access-Control-Allow-Origin: *` allows any website to make cross-origin API calls.

**Recommendation:** Restrict to the CloudFront domain or make it configurable.

#### 2c. No Rate Limiting

- No WAF on CloudFront distribution.
- No `ReservedConcurrentExecutions` on Lambda.
- CloudFront API cache behavior has `TTL=0` at all levels, so every request hits Lambda.

**Recommendation:** Add Lambda concurrency limits and CloudFront WAF with rate-limiting rules.

#### 2d. No Input Validation (`lambda_bridge.py:29-39`)

The Lambda handler passes raw HTTP body to `invoke_agent_runtime` without validation.

**Recommendation:** Add payload size limits and basic schema validation.

---

### 3. Resource Management and Deployment Error Mitigation

**Risk Level: MODERATE**

#### State Management
- Local JSON state file is the only record of deployed resources.
- No locking mechanism for concurrent deploys.
- No CloudFormation/CDK: raw boto3 calls with no atomic rollback.

#### Destroy Deletes State Unconditionally (`destroy.py:153`)
If a resource fails to delete, the state file is still wiped, causing permanent resource orphaning.

**Recommendation:**
1. Only remove state entries for successfully deleted resources.
2. Add `--force` flag for unconditional state wipe.
3. Consider a `sss status --reconcile` to rediscover resources.

#### IAM Role Policy Not Updated on Re-deploy (`agentcore.py:88-90`)
When a role already exists, `create_iam_role` returns early without updating the inline policy.

---

### 4. Deployment Confidence and Cost Visibility

**Risk Level: HIGH**

- No CloudWatch alarms, AWS Budgets integration, or dashboards.
- No resource tagging for cost allocation.
- No Lambda concurrency limit -- unbounded Bedrock model invocation costs.
- `PriceClass_100` on CloudFront is good (cheapest regions).
- `status.py` only checks resource existence, not usage or cost metrics.

**Recommendation:** Add Lambda concurrency limits, resource tags, and document expected costs.

---

### 5. Python Typing Quality

**Overall: GOOD**

**Strengths:**
- All public functions fully annotated with modern `str | None` syntax.
- Zero `Any` usage, zero `# type: ignore` comments.
- `from __future__ import annotations` in every file.
- Dataclasses fully typed with proper defaults.

**Weaknesses:**
- `callable` (lowercase) used instead of `Callable` at `s3.py:81`.
- ~8 private functions have untyped boto3 client parameters.
- Return types use loose `dict` instead of `TypedDict`.
- No mypy/pyright configured in `pyproject.toml`.

---

### 6. Test Quality

**Overall: MIXED -- good structure, significant coverage gaps**

**Strengths:**
- Good test independence via `tmp_path` and `@mock_aws`.
- Meaningful assertions testing actual behavior.
- Appropriate moto usage for S3/IAM.
- Edge cases covered (idempotency, nonexistent resources, invalid inputs).

**Critical Gaps:**
- `deploy.py` (188 lines): **ZERO tests**
- `destroy.py` (155 lines): **ZERO direct tests**
- `cloudfront.py` (256 lines): **ZERO tests**
- `lambda_bridge.py` (293 lines): **ZERO tests**
- `cf_function.py`: **ZERO tests**
- `status.py`: **ZERO direct tests**
- No integration test for full deploy->status->destroy lifecycle.
- Embedded Lambda handler code never executed in tests.

---

## Summary by Severity

### Critical
| # | Finding | Location |
|---|---------|----------|
| 1 | Unauthenticated public Lambda Function URL | `lambda_bridge.py:249-261` |
| 2 | No rate limiting on any layer | `cloudfront.py`, `lambda_bridge.py` |
| 3 | Wildcard CORS on API responses | `lambda_bridge.py:53` |

### High
| # | Finding | Location |
|---|---------|----------|
| 4 | `destroy` deletes state file even on partial failure | `destroy.py:153` |
| 5 | No tests for deploy/destroy/cloudfront/lambda_bridge | `tests/` |
| 6 | No Lambda concurrency limit (unbounded cost) | `lambda_bridge.py:158` |

### Medium
| # | Finding | Location |
|---|---------|----------|
| 7 | No WAF on CloudFront distribution | `cloudfront.py:145-159` |
| 8 | No resource tagging | All AWS modules |
| 9 | IAM role policy not updated on re-deploy | `agentcore.py:88-90` |
| 10 | No input validation in Lambda handler | `lambda_bridge.py:29-39` |

### Low
| # | Finding | Location |
|---|---------|----------|
| 11 | `callable` (lowercase) instead of `Callable` | `s3.py:81` |
| 12 | Untyped boto3 client params in private functions | Multiple files |
| 13 | No mypy/pyright configured | `pyproject.toml` |
| 14 | `AWS_DEFAULT_REGION` not cleaned up in test teardown | `conftest.py:20-26` |

## Alternatives Considered

N/A -- this is a review document, not an implementation proposal.

## Implementation Plan

Prioritized remediation order:
1. **Fix authentication:** Add auth to Lambda Function URL or add a shared-secret header.
2. **Add rate limiting:** Set Lambda `ReservedConcurrentExecutions` and consider WAF.
3. **Restrict CORS:** Replace wildcard with CloudFront domain.
4. **Fix destroy state management:** Only delete state for successful resource deletions.
5. **Add critical tests:** Deploy, destroy, cloudfront, and lambda_bridge modules.
6. **Add input validation:** Validate payload in Lambda handler.
7. **Add resource tags and cost monitoring.**
8. **Fix typing issues:** `callable` -> `Callable`, type boto3 params, add mypy config.
