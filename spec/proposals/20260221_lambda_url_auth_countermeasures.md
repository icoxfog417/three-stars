# Proposal: Lambda Function URL Authentication Countermeasures

**Date**: 2026-02-21
**Author**: Claude Agent
**Status**: Proposed

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

### Evaluation

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Security | **Strong** | Cryptographic SigV4, direct URL access blocked |
| Streaming | **Yes** | Lambda Function URL RESPONSE_STREAM works with OAC |
| Parallel deploy | **No** | CloudFront still couples frontend + backend |
| Complexity | **Low-Medium** | Reuses existing OAC pattern, but client-side SHA256 is friction |
| Cost | **Free** | No additional AWS charges |
| Client burden | **High** | Every POST must compute SHA256 hash |

---

## Option 2: API Gateway (HTTP API)

Replace Lambda Function URL with API Gateway HTTP API as the backend entry point.
This naturally separates frontend and backend into independent deployment units.

### Architecture

```
                         CloudFront (frontend only)
Browser ─→ CloudFront ─→ /* ─→ S3 (OAC)
         │
         └─→ API Gateway (HTTP API) ─→ Lambda ─→ AgentCore
              ↑ Built-in throttling, auth, WAF-ready
              ↑ Own URL: https://{api-id}.execute-api.{region}.amazonaws.com
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

With separate domains, CORS is required. API Gateway HTTP API has built-in CORS
configuration (no need to handle in Lambda code):

```python
# API Gateway handles CORS automatically via configuration
# Remove CORS headers from Lambda handler entirely
cors_config = {
    "AllowOrigins": [f"https://{cloudfront_domain}"],
    "AllowMethods": ["POST", "OPTIONS"],
    "AllowHeaders": ["Content-Type"],
}
```

This also **fixes the wildcard CORS issue** (finding #3 from the review) by design.

### Streaming Support

API Gateway HTTP API supports Lambda response streaming (InvokeMode=RESPONSE_STREAM).
This was added in late 2024 and is GA. Key constraints:
- Maximum response payload: 20 MB (sufficient for AI agent text responses)
- Chunked transfer encoding
- Works with standard `fetch()` + `ReadableStream` on the client side

### Authentication Options

API Gateway HTTP API supports:
- **IAM auth** — SigV4 from the client (complex for browser apps)
- **JWT authorizer** — Validate JWTs from identity providers (future auth feature)
- **API keys** — Simple throttling per key
- **None + Lambda authorizer** — Custom auth logic
- **None + WAF** — Rate limiting at the edge

For the initial implementation, `None` auth + Lambda concurrency limits provides
equivalent security to the current setup, with the path to add proper auth later.

### Evaluation

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Security | **Good+** | Built-in throttling, WAF-ready, auth extensible |
| Streaming | **Yes** | HTTP API supports RESPONSE_STREAM (20 MB limit) |
| Parallel deploy | **Yes** | Frontend and backend fully independent |
| Complexity | **Medium** | New `aws/apigateway.py` module, route setup |
| Cost | **$1/M requests** | HTTP API pricing, free tier: 1M req/month for 12 months |
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

| | OAC for Lambda | API Gateway | Shared Secret |
|---|---|---|---|
| **Security** | Strong (SigV4) | Good (throttling, WAF path) | Weak (obscurity) |
| **Streaming** | Yes | Yes (20 MB limit) | Yes |
| **Parallel deploy** | No | **Yes** | No |
| **Client SHA256 needed** | **Yes (friction)** | No | No |
| **Fixes wildcard CORS** | No (manual) | **Yes (built-in)** | No (manual) |
| **Rate limiting** | Need Lambda concurrency | **Built-in** | Need Lambda concurrency |
| **Future auth path** | Limited | **JWT, API keys, WAF** | Limited |
| **Implementation size** | ~50 lines changed | ~200 lines new module | ~20 lines changed |
| **Additional cost** | Free | $1/M requests | Free |
| **Deploy speed improvement** | None | **Frontend: seconds** | None |

## Recommendation

**API Gateway (Option 2)** is recommended as the primary approach because:

1. **Parallel deployment** is a real developer-experience win — frontend iteration drops
   from minutes to seconds.
2. **No client-side SHA256** — OAC's POST requirement adds unavoidable friction to every
   API call and every user's frontend code.
3. **Built-in rate limiting** solves review finding #2 (no rate limiting) by default.
4. **Built-in CORS** solves review finding #3 (wildcard CORS) by design.
5. **Future-proof auth** — JWT authorizers, API keys, and WAF integration provide a
   clear upgrade path.
6. **20 MB streaming limit** is more than sufficient for text-based AI agent responses.

The $1/M request cost is negligible for a developer tool, and the first 1M requests/month
are free for 12 months.

### Implementation Plan (if approved)

1. Create `src/three_stars/aws/apigateway.py` — HTTP API, route, integration, stage
2. Update `lambda_bridge.py` — remove Function URL creation, remove `Principal=*`
3. Update `deploy.py` — split into parallel frontend/backend pipelines
4. Update `destroy.py` — add API Gateway teardown
5. Update `cloudfront.py` — remove Lambda origin (CloudFront serves frontend only)
6. Update `cli.py` — add `--frontend`/`--backend` flags to `deploy`
7. Update Lambda handler — remove CORS headers (API Gateway handles it)
8. Update state model — add API Gateway resource IDs
9. Update starter template — API URL from config instead of relative `/api/*`

## Alternatives Considered

See Options 1 and 3 above. Option 3 (shared secret) could serve as a **quick interim fix**
while Option 2 is implemented, since it's only ~20 lines of code. However, it should not
be considered a permanent solution.
