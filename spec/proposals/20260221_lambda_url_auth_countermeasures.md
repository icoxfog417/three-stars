# Proposal: Lambda Function URL Authentication Countermeasures

**Date**: 2026-02-21
**Author**: Claude Agent
**Status**: Approved (Option 1a: OAC + Lambda@Edge)

## Background

The security review identified that the Lambda Function URL uses `AuthType=NONE` with
`Principal=*` (`lambda_bridge.py:249-261`), making it publicly invocable by anyone who
discovers the URL — bypassing CloudFront entirely.

This proposal compares three countermeasures, considering:
- Security strength
- Streaming compatibility (AI agent responses)
- Frontend/backend deployment separation
- Implementation complexity and cost

## Current Architecture

```
Browser ─→ CloudFront ─→ /* ──────→ S3 (frontend)
                       └→ /api/* ─→ Lambda Function URL (AuthType=NONE) ─→ AgentCore
                                     ↑ PUBLICLY ACCESSIBLE (the problem)
```

Deploy is fully serial: IAM → S3 → Frontend → AgentCore → Lambda → CloudFront.
CloudFront creation depends on both S3 bucket AND Lambda Function URL being ready.

---

## Option 1: CloudFront OAC for Lambda Function URL

Use CloudFront's native Origin Access Control to sign requests to Lambda with SigV4.
This is the simplified version of "Lambda@Edge + SigV4" — no Lambda@Edge needed.

### Architecture

```
Browser ─→ CloudFront ─→ /* ──────→ S3 (OAC, existing)
             (signs      └→ /api/* ─→ Lambda Function URL (AuthType=AWS_IAM)
              with SigV4)              ↑ Only accepts CloudFront-signed requests
```

### Changes Required

1. Create OAC with `OriginAccessControlOriginType="lambda"` (separate from S3 OAC)
2. Change Lambda Function URL to `AuthType=AWS_IAM`
3. Replace `Principal=*` with resource policy granting `cloudfront.amazonaws.com`
   conditioned on distribution ARN
4. **Client-side change required** (see gotcha below)

### Critical Gotcha: POST/PUT SHA256 Requirement

AWS documentation states:

> If you use PUT or POST methods with your Lambda function URL, **your users must compute
> the SHA256 of the body** and include the payload hash value of the request body in the
> `x-amz-content-sha256` header when sending the request to CloudFront. Lambda doesn't
> support unsigned payloads.

This means the **browser JavaScript must**:
```javascript
// Every API call from the frontend needs this:
const body = JSON.stringify({ message: "Hello" });
const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(body));
const hashHex = Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, "0")).join("");

fetch("/api/chat", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "x-amz-content-sha256": hashHex,  // Required!
  },
  body,
});
```

This adds friction to every API call from the frontend. The starter template and
all user frontend code must include this pattern.

### Variant 1a: Lambda@Edge computes SHA256

Instead of requiring the client to compute SHA256, use Lambda@Edge on the
origin-request event to compute it server-side:

```
Browser ─→ CloudFront ─→ Lambda@Edge (origin-request) ─→ Lambda Function URL
             (OAC)        reads body, computes SHA256,      (AuthType=AWS_IAM)
                          sets x-amz-content-sha256 header
```

This eliminates the client burden entirely. The Lambda@Edge function is small:

```javascript
exports.handler = async (event) => {
  const request = event.Records[0].cf.request;
  if (request.body && request.body.data) {
    const crypto = require('crypto');
    const bodyData = Buffer.from(request.body.data, request.body.encoding);
    const hash = crypto.createHash('sha256').update(bodyData).digest('hex');
    request.headers['x-amz-content-sha256'] = [{ key: 'x-amz-content-sha256', value: hash }];
  }
  return request;
};
```

**Key constraint**: Lambda@Edge "include body" option limits request body to **~1 MB**.
For AI agent text prompts this is likely sufficient (1 MB ≈ ~500K characters of text),
but it is a hard ceiling — requests with bodies exceeding 1 MB will be truncated.

**Lambda@Edge does NOT interfere with response streaming**: it only runs on the
origin-request event. The response streams directly from the Function URL through
CloudFront to the client.

### Evaluation

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Security | **Strong** | Cryptographic SigV4, direct URL access blocked |
| Streaming | **Yes** | Lambda Function URL RESPONSE_STREAM works with OAC |
| Parallel deploy | **No** | CloudFront still couples frontend + backend |
| Complexity | **Low-Medium** | OAC + small Lambda@Edge function |
| Cost | **~Free** | Lambda@Edge pricing is negligible at low volume |
| Client burden | **None** (with 1a) | Lambda@Edge handles SHA256 transparently |
| Request body limit | **~1 MB** | Lambda@Edge "include body" hard limit |

---

## Option 2: API Gateway (REST API)

Replace Lambda Function URL with API Gateway REST API as the backend entry point.
This naturally separates frontend and backend into independent deployment units.

**Why REST API, not HTTP API**: HTTP API does NOT support response streaming.
REST API gained streaming support in November 2025 (`transferMode=STREAM`).
For an AI agent that produces incremental text output, streaming is essential.

### Architecture

```
                         CloudFront (frontend only)
Browser ─→ CloudFront ─→ /* ─→ S3 (OAC)
         │
         └─→ API Gateway (REST API) ─→ Lambda (RESPONSE_STREAM) ─→ AgentCore
              ↑ Built-in throttling, WAF, auth
              ↑ Own URL: https://{api-id}.execute-api.{region}.amazonaws.com/{stage}
```

The frontend calls the API Gateway URL directly (or via CloudFront as a second origin —
but separating them is the key advantage).

### Parallel Deployment

This is the major architectural benefit. Deploy becomes two independent pipelines:

```
Frontend pipeline:          Backend pipeline:
  S3 bucket                   IAM roles
  Upload files                AgentCore runtime + endpoint
  CloudFront (S3 only)        Lambda function
  ~10 seconds                 API Gateway
                              ~2-3 minutes

Can run fully in parallel. No shared dependency.
```

Enables new CLI patterns:
- `sss deploy` — both pipelines in parallel
- `sss deploy --frontend` — just S3 + CloudFront (seconds)
- `sss deploy --backend` — just Lambda + API Gateway + AgentCore

Frontend iteration becomes near-instant (S3 upload + optional CloudFront invalidation)
without touching any backend resources.

### CORS Handling

With separate domains, CORS is required. REST API supports CORS configuration
via method responses and gateway responses (or via Lambda proxy integration).

This also **fixes the wildcard CORS issue** (finding #3 from the review) — the
allowed origin is set to the CloudFront domain explicitly.

### Streaming Support

REST API supports Lambda response streaming since November 2025:
- Set `transferMode=STREAM` on the integration
- Uses `InvokeWithResponseStream` Lambda API internally
- First 10 MB: no bandwidth restriction; beyond: 2 MB/s limit
- Idle timeout: 30 seconds (edge-optimized) or 5 minutes (regional)
- Max streaming duration: 15 minutes
- Does not support endpoint caching or content encoding while streaming

**Important**: Browser `fetch()` API may buffer streamed responses in some cases.
`EventSource` or `ReadableStream` with explicit chunked handling is recommended.

### Authentication Options

API Gateway REST API supports:
- **IAM auth** — SigV4 from the client (complex for browser apps)
- **Cognito authorizer** — User pool-based auth
- **API keys + usage plans** — Throttling per key
- **Lambda authorizer** — Custom auth logic (token or request-based)
- **WAF integration** — Rate limiting, IP blocking, geo-restriction

For the initial implementation, `NONE` auth + Lambda concurrency limits provides
equivalent security to the current setup, with a clear path to add Cognito or
API keys later.

### Evaluation

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Security | **Good+** | Built-in throttling, WAF, auth extensible |
| Streaming | **Yes** | REST API streaming since Nov 2025 (10 MB unrestricted, then 2 MB/s) |
| Parallel deploy | **Yes** | Frontend and backend fully independent |
| Complexity | **Medium-High** | New `aws/apigateway.py` module, REST API has more config than HTTP API |
| Cost | **$3.50/M requests** | REST API pricing (more expensive than HTTP API's $1/M) |
| Client burden | **None** | Standard `fetch()` calls |

---

## Option 3: Custom Origin Header (Shared Secret)

Add a secret header in CloudFront origin configuration; validate it in the Lambda handler.
CloudFront automatically attaches the header to every origin request.

### Architecture

```
Browser ─→ CloudFront ─→ /api/* ─→ Lambda Function URL (AuthType=NONE)
             adds header:            validates:
             x-origin-verify: {s}    if header != {s}: return 403
```

### Changes Required

1. Generate a random secret at deploy time, store in state file
2. Add `CustomHeaders` to Lambda origin in CloudFront distribution config:
   ```python
   "CustomHeaders": {
       "Quantity": 1,
       "Items": [{"HeaderName": "x-origin-verify", "HeaderValue": secret}]
   }
   ```
3. Add validation in Lambda handler:
   ```python
   expected = os.environ.get("ORIGIN_VERIFY_SECRET")
   actual = event.get("headers", {}).get("x-origin-verify")
   if actual != expected:
       return {"statusCode": 403, "body": "Forbidden"}
   ```

### Evaluation

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Security | **Weak** | Obscurity-based. Secret visible in CF console. No crypto. |
| Streaming | **Yes** | No change to Lambda invocation |
| Parallel deploy | **No** | CloudFront still couples frontend + backend |
| Complexity | **Very Low** | ~20 lines of code total |
| Cost | **Free** | No additional charges |
| Client burden | **None** | Transparent to frontend |

---

## Comparison Summary

| | OAC + Lambda@Edge (1a) | API Gateway (REST) | Shared Secret |
|---|---|---|---|
| **Security** | **Strong (SigV4)** | Good (throttling, WAF) | Weak (obscurity) |
| **Streaming** | Yes (Function URL) | Yes (since Nov 2025, 10MB+2MB/s) | Yes |
| **Parallel deploy** | No | **Yes** | No |
| **Client SHA256 needed** | **No (Lambda@Edge)** | No | No |
| **Fixes wildcard CORS** | No (manual) | **Yes** | No (manual) |
| **Rate limiting** | Need Lambda concurrency | **Built-in (usage plans)** | Need Lambda concurrency |
| **Future auth path** | Limited | **Cognito, API keys, WAF** | Limited |
| **Implementation size** | ~80 lines changed | ~250 lines new module | ~20 lines changed |
| **Additional cost** | ~Free | $3.50/M requests | Free |
| **Deploy speed improvement** | None | **Frontend: seconds** | None |
| **Request body limit** | **~1 MB (Lambda@Edge)** | 10 MB (REST API) | 6 MB (Function URL) |

## Decision

**Option 1a (OAC + Lambda@Edge)** is chosen as the minimum viable approach:

- Strongest security (SigV4 cryptographic signing)
- No client-side burden (Lambda@Edge handles SHA256)
- Minimal code change (~80 lines)
- No new AWS services or pricing tiers
- Lambda@Edge deploy is one-time (function never changes)

Option 2 (API Gateway REST API) remains a viable future upgrade if parallel deployment
or built-in rate limiting becomes a priority.

### Implementation Plan

**Sequence** (respects the chicken-and-egg dependency: CloudFront ARN needed for Lambda
permission, but CloudFront needs Lambda Function URL to create distribution):

1. **lambda_bridge.py** — Lambda@Edge function + IAM role
   - Create `create_edge_role()`: IAM role with `lambda.amazonaws.com` + `edgelambda.amazonaws.com` trust
   - Create `create_edge_function()`: SHA256 hasher in us-east-1, publish version
   - Create `delete_edge_function()` and `delete_edge_role()` for teardown

2. **lambda_bridge.py** — Lambda Function URL auth change
   - Change `_ensure_function_url()`: `AuthType="NONE"` → `AuthType="AWS_IAM"`
   - Remove `add_permission(Principal="*")` call
   - Add `grant_cloudfront_access(function_name, distribution_arn)`: grants
     `cloudfront.amazonaws.com` with `AWS:SourceArn` condition

3. **cloudfront.py** — Lambda OAC + Lambda@Edge association
   - Add `create_lambda_oac()`: OAC with `OriginAccessControlOriginType="lambda"`
   - Modify Lambda origin: attach OAC ID
   - Modify `/api/*` cache behavior: add `LambdaFunctionAssociations` for
     origin-request with `IncludeBody=True`

4. **deploy.py** — Updated deployment order
   - Step 7: Create Lambda (AuthType=AWS_IAM, no public permission yet)
   - Step 7.5: Create Lambda@Edge role + function (us-east-1)
   - Step 8: Create CloudFront with Lambda OAC + Lambda@Edge association
   - Step 8.5: Grant CloudFront → Lambda permission (now we have distribution ARN)

5. **destroy.py** — Updated teardown
   - Add: delete Lambda@Edge function + role (after CloudFront deletion)
   - Add: delete Lambda OAC

6. **State model** — New fields
   - `edge_function_arn` (versioned ARN for CloudFront association)
   - `edge_role_name`, `edge_role_arn`
   - `lambda_oac_id`

## Alternatives Considered

- **Option 2 (API Gateway REST API)**: More features (WAF, throttling, parallel deploy)
  but higher complexity and $3.50/M cost. Reserved for future if needed.
- **Option 3 (Shared Secret)**: Too weak for production. Only ~20 lines but
  obscurity-based, no crypto guarantee.
