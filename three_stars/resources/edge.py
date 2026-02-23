"""Edge resource module — Lambda@Edge SigV4 signer + IAM role (us-east-1).

The Lambda@Edge function signs origin requests with SigV4 so CloudFront can
forward them directly to AgentCore.  It also rewrites the URL path from
``/api/invoke`` to the AgentCore invocation endpoint.  The runtime ARN and
region are embedded in the code at deploy time (Lambda@Edge cannot use custom
environment variables).
"""

from __future__ import annotations

import io
import json
import time
import zipfile

from botocore.exceptions import ClientError

from three_stars.naming import ResourceNames
from three_stars.resources import ResourceStatus
from three_stars.resources._base import AWSContext
from three_stars.state import EdgeState

# {RUNTIME_ARN} and {REGION} are replaced at deploy time.
_EDGE_FUNCTION_CODE = """\
import base64
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

RUNTIME_ARN = "{RUNTIME_ARN}"
REGION = "{REGION}"
SERVICE = "bedrock-agentcore"
HOST = SERVICE + "." + REGION + ".amazonaws.com"


def _hmac_sha256(key, data):
    if isinstance(key, str):
        key = key.encode("utf-8")
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hmac.new(key, data, hashlib.sha256).digest()


def _sha256_hex(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def handler(event, context):
    request = event["Records"][0]["cf"]["request"]

    # --- Body ---
    body_bytes = b""
    body = request.get("body")
    if body and body.get("data"):
        encoding = body.get("encoding", "base64")
        if encoding == "base64":
            body_bytes = base64.b64decode(body["data"])
        else:
            body_bytes = body["data"].encode("utf-8")
    payload_hash = _sha256_hex(body_bytes)

    # --- Session ID ---
    try:
        parsed = json.loads(body_bytes.decode("utf-8"))
        session_id = parsed.get("session_id") or str(uuid.uuid4())
        # Write resolved session_id back so the agent handler can read it
        parsed["session_id"] = session_id
        body_bytes = json.dumps(parsed).encode("utf-8")
        payload_hash = _sha256_hex(body_bytes)
    except Exception:
        session_id = str(uuid.uuid4())

    # --- Rewrite URL ---
    encoded_arn = quote(RUNTIME_ARN, safe="")
    request["uri"] = "/runtimes/" + encoded_arn + "/invocations"
    request["querystring"] = "qualifier=DEFAULT"
    request["origin"] = {
        "custom": {
            "domainName": HOST,
            "port": 443,
            "protocol": "https",
            "path": "",
            "sslProtocols": ["TLSv1.2"],
            "readTimeout": 60,
            "keepaliveTimeout": 5,
            "customHeaders": {},
        }
    }

    # --- SigV4 Signing ---
    method = request.get("method", "POST")
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = amz_date[:8]
    scope = date_stamp + "/" + REGION + "/" + SERVICE + "/aws4_request"

    # Headers to sign
    headers = {
        "host": HOST,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "x-amzn-bedrock-agentcore-runtime-session-id": session_id,
        "content-type": "application/json",
    }

    # Add security token if present (Lambda uses temp credentials)
    token = os.environ.get("AWS_SESSION_TOKEN")
    if token:
        headers["x-amz-security-token"] = token

    signed_header_keys = sorted(headers.keys())
    signed_headers = ";".join(signed_header_keys)
    canonical_headers = "".join(k + ":" + headers[k] + "\\n" for k in signed_header_keys)

    # SigV4 requires double-URI-encoding of path segments (except S3).
    # request["uri"] is single-encoded for the HTTP request; re-encode each
    # segment so %xx sequences become %25xx in the canonical request.
    canonical_uri = "/".join(quote(s, safe="") for s in request["uri"].split("/"))

    canonical_request = "\\n".join([
        method,
        canonical_uri,
        request["querystring"],
        canonical_headers,
        signed_headers,
        payload_hash,
    ])

    string_to_sign = "\\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        scope,
        _sha256_hex(canonical_request),
    ])

    # Derive signing key
    key = _hmac_sha256("AWS4" + os.environ["AWS_SECRET_ACCESS_KEY"], date_stamp)
    key = _hmac_sha256(key, REGION)
    key = _hmac_sha256(key, SERVICE)
    key = _hmac_sha256(key, "aws4_request")

    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    auth_header = (
        "AWS4-HMAC-SHA256 Credential="
        + os.environ["AWS_ACCESS_KEY_ID"] + "/" + scope
        + ", SignedHeaders=" + signed_headers
        + ", Signature=" + signature
    )

    # --- Apply headers to CloudFront request ---
    request["headers"]["host"] = [{"key": "Host", "value": HOST}]
    request["headers"]["authorization"] = [{"key": "Authorization", "value": auth_header}]
    request["headers"]["x-amz-date"] = [{"key": "X-Amz-Date", "value": amz_date}]
    request["headers"]["x-amz-content-sha256"] = [
        {"key": "X-Amz-Content-Sha256", "value": payload_hash}
    ]
    request["headers"]["x-amzn-bedrock-agentcore-runtime-session-id"] = [
        {"key": "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id", "value": session_id}
    ]
    request["headers"]["content-type"] = [{"key": "Content-Type", "value": "application/json"}]

    if token:
        request["headers"]["x-amz-security-token"] = [
            {"key": "X-Amz-Security-Token", "value": token}
        ]

    # Update the request body with the (possibly modified) payload
    if body_bytes:
        request["body"] = {
            "inputTruncated": False,
            "action": "replace",
            "encoding": "text",
            "data": body_bytes.decode("utf-8"),
        }

    return request
"""


def deploy(
    ctx: AWSContext,
    names: ResourceNames,
    *,
    runtime_arn: str,
    region: str,
    tags: list[dict[str, str]] | None = None,
    tags_dict: dict[str, str] | None = None,
    existing: EdgeState | None = None,
) -> EdgeState:
    """Create or update Lambda@Edge function + IAM role in us-east-1.

    Args:
        runtime_arn: AgentCore runtime ARN to embed in edge code.
        region: AWS region where AgentCore is deployed.
        tags: AWS tag list format for IAM roles.
        tags_dict: Dict format tags for Lambda functions.
        existing: Existing state if updating (skips role creation, updates code).
    """
    if existing:
        # Always update the function code so the embedded runtime ARN stays current
        function_code = _render_code(runtime_arn, region)
        _update_edge_function(ctx, existing.function_name, function_code)
        return existing

    role_arn = _create_edge_role(ctx, names.edge_role, region, tags=tags)

    function_code = _render_code(runtime_arn, region)
    function_arn = _create_edge_function(
        ctx, names.edge_function, role_arn, function_code, tags=tags_dict
    )

    return EdgeState(
        role_name=names.edge_role,
        role_arn=role_arn,
        function_name=names.edge_function,
        function_arn=function_arn,
    )


def destroy(ctx: AWSContext, state: EdgeState) -> bool:
    """Delete Lambda@Edge function and IAM role.

    Returns ``True`` if all resources were deleted.  Returns ``False`` if the
    Lambda function still has replicas — the function will become deletable
    once AWS finishes replica cleanup (typically 30-60 minutes).

    The IAM role is always deleted immediately since it is not blocked by
    replica cleanup (only the function deletion is gated on replicas).
    """
    deleted = _delete_edge_function(ctx, state.function_name)
    _delete_edge_role(ctx, state.role_name)
    return deleted


def get_status(ctx: AWSContext, state: EdgeState) -> list[ResourceStatus]:
    """Return Lambda@Edge status."""
    rows: list[ResourceStatus] = []

    name = "Lambda@Edge"
    fn = state.function_name
    try:
        lam = ctx.client("lambda", region_name="us-east-1")
        resp = lam.get_function(FunctionName=fn)
        fn_state = resp["Configuration"]["State"]
        if fn_state == "Active":
            rows.append(ResourceStatus(name, fn, "[green]Active[/green]"))
        else:
            rows.append(ResourceStatus(name, fn, f"[yellow]{fn_state}[/yellow]"))
    except Exception:
        rows.append(ResourceStatus(name, fn, "[red]Not Found[/red]"))

    return rows


def _render_code(runtime_arn: str, region: str) -> str:
    """Substitute runtime ARN and region into the edge function template."""
    return _EDGE_FUNCTION_CODE.replace("{RUNTIME_ARN}", runtime_arn).replace("{REGION}", region)


def _create_edge_role(
    ctx: AWSContext,
    role_name: str,
    region: str,
    tags: list[dict[str, str]] | None = None,
) -> str:
    """Create an IAM role for the Lambda@Edge function. Returns the role ARN."""
    iam = ctx.client("iam")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": [
                        "lambda.amazonaws.com",
                        "edgelambda.amazonaws.com",
                    ]
                },
                "Action": "sts:AssumeRole",
            }
        ],
    }

    created = False
    try:
        create_kwargs: dict = {
            "RoleName": role_name,
            "AssumeRolePolicyDocument": json.dumps(trust_policy),
            "Description": "Execution role for three-stars Lambda@Edge SigV4 signer",
        }
        if tags:
            create_kwargs["Tags"] = tags
        resp = iam.create_role(**create_kwargs)
        role_arn = resp["Role"]["Arn"]
        created = True
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            resp = iam.get_role(RoleName=role_name)
            role_arn = resp["Role"]["Arn"]
            if tags:
                iam.tag_role(RoleName=role_name, Tags=tags)
        else:
            raise

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="lambda-edge-execution",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "logs:CreateLogGroup",
                            "logs:CreateLogStream",
                            "logs:PutLogEvents",
                        ],
                        "Resource": "arn:aws:logs:*:*:*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                        "Resource": "*",
                    },
                ],
            }
        ),
    )

    if created:
        time.sleep(10)
    return role_arn


def _create_edge_function(
    ctx: AWSContext,
    function_name: str,
    role_arn: str,
    function_code: str,
    tags: dict[str, str] | None = None,
) -> str:
    """Create a Lambda@Edge function in us-east-1. Returns versioned ARN."""
    lam = ctx.client("lambda", region_name="us-east-1")
    zip_bytes = _zip_code(function_code)

    try:
        create_kwargs: dict = {
            "FunctionName": function_name,
            "Runtime": "python3.12",
            "Role": role_arn,
            "Handler": "index.handler",
            "Code": {"ZipFile": zip_bytes},
            "Timeout": 5,
            "MemorySize": 128,
            "Description": "SigV4 signer for CloudFront → AgentCore requests",
        }
        if tags:
            create_kwargs["Tags"] = tags
        lam.create_function(**create_kwargs)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            _wait_for_lambda_active(lam, function_name)
            lam.update_function_code(FunctionName=function_name, ZipFile=zip_bytes)
        else:
            raise

    _wait_for_lambda_active(lam, function_name)

    resp = lam.publish_version(
        FunctionName=function_name,
        Description="SigV4 edge signer",
    )

    return resp["FunctionArn"]


def _update_edge_function(
    ctx: AWSContext,
    function_name: str,
    function_code: str,
) -> str:
    """Update existing Lambda@Edge function code and publish a new version."""
    lam = ctx.client("lambda", region_name="us-east-1")
    zip_bytes = _zip_code(function_code)

    _wait_for_lambda_active(lam, function_name)
    lam.update_function_code(FunctionName=function_name, ZipFile=zip_bytes)
    _wait_for_lambda_active(lam, function_name)

    resp = lam.publish_version(
        FunctionName=function_name,
        Description="SigV4 edge signer",
    )
    return resp["FunctionArn"]


def _zip_code(code: str) -> bytes:
    """Package Python code into a zip archive."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.py", code)
    return buffer.getvalue()


def _wait_for_lambda_active(
    lam,
    function_name: str,
    max_wait_seconds: int = 60,
    poll_interval: int = 2,
) -> None:
    """Wait for Lambda function to reach Active state."""
    start = time.time()
    while time.time() - start < max_wait_seconds:
        resp = lam.get_function(FunctionName=function_name)
        config = resp["Configuration"]
        state = config["State"]
        last_update = config.get("LastUpdateStatus", "Successful")
        if state == "Active" and last_update in ("Successful", None):
            return
        if state == "Failed":
            raise RuntimeError(f"Lambda@Edge {function_name} failed: {config.get('StateReason')}")
        time.sleep(poll_interval)

    raise TimeoutError(
        f"Lambda@Edge {function_name} did not become Active within {max_wait_seconds}s"
    )


def _delete_edge_function(ctx: AWSContext, function_name: str) -> bool:
    """Try to delete the Lambda@Edge function from us-east-1.

    Lambda@Edge replicas are cleaned up asynchronously by AWS after the
    function is disassociated from CloudFront.  This can take minutes to
    hours.  Rather than blocking, we attempt deletion once:

    - If it succeeds, return ``True``.
    - If replicas still exist, return ``False`` (caller should inform user).
    - If the function doesn't exist, return ``True``.
    """
    lam = ctx.client("lambda", region_name="us-east-1")
    try:
        lam.delete_function(FunctionName=function_name)
        return True
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            return True
        if code == "InvalidParameterValueException" and "replicated" in str(e):
            return False
        raise


def _delete_edge_role(ctx: AWSContext, role_name: str) -> None:
    """Delete the Lambda@Edge IAM role and its inline policies."""
    iam = ctx.client("iam")
    try:
        policies = iam.list_role_policies(RoleName=role_name)
        for policy_name in policies.get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        iam.delete_role(RoleName=role_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            return
        raise
