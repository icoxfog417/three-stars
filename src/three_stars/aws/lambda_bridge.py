"""Lambda function bridge for AgentCore invocation.

Creates a Lambda function with a function URL that bridges HTTP requests
from CloudFront to the AgentCore invoke_agent_runtime SDK call.
"""

from __future__ import annotations

import io
import json
import time
import zipfile

import boto3
from botocore.exceptions import ClientError

# Embedded Lambda function code for the bridge
_BRIDGE_FUNCTION_CODE = '''\
import json
import os
import boto3


def handler(event, context):
    """Bridge HTTP requests to AgentCore invoke_agent_runtime."""
    client = boto3.client("bedrock-agentcore")

    # Parse body from Lambda function URL event
    body = event.get("body", "{}")
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode()

    runtime_arn = os.environ["AGENT_RUNTIME_ARN"]

    try:
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            payload=body.encode("utf-8") if isinstance(body, str) else body,
            contentType="application/json",
        )

        response_body = resp["response"].read().decode("utf-8")
        status_code = resp.get("statusCode", 200)
    except Exception as e:
        response_body = json.dumps({"message": f"Agent invocation error: {e}"})
        status_code = 500

    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
        "body": response_body,
    }
'''


def _build_function_zip() -> bytes:
    """Package the bridge function code into a zip."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.py", _BRIDGE_FUNCTION_CODE)
    return buffer.getvalue()


def create_lambda_role(
    session: boto3.Session,
    role_name: str,
    account_id: str,
    region: str,
    tags: list[dict[str, str]] | None = None,
) -> str:
    """Create an IAM role for the Lambda bridge function.

    Returns the role ARN.
    """
    iam = session.client("iam")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    try:
        create_kwargs: dict = {
            "RoleName": role_name,
            "AssumeRolePolicyDocument": json.dumps(trust_policy),
            "Description": "Execution role for three-stars Lambda bridge",
        }
        if tags:
            create_kwargs["Tags"] = tags
        resp = iam.create_role(**create_kwargs)
        role_arn = resp["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            resp = iam.get_role(RoleName=role_name)
            role_arn = resp["Role"]["Arn"]
            if tags:
                iam.tag_role(RoleName=role_name, Tags=tags)
            return role_arn
        raise

    # Attach basic Lambda execution + AgentCore invoke permissions
    inline_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": f"arn:aws:logs:{region}:{account_id}:*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:InvokeAgentRuntime",
                ],
                "Resource": "*",
            },
        ],
    }

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="three-stars-lambda-bridge-policy",
        PolicyDocument=json.dumps(inline_policy),
    )

    # Wait for role propagation
    time.sleep(10)

    return role_arn


def create_lambda_function(
    session: boto3.Session,
    function_name: str,
    role_arn: str,
    agent_runtime_arn: str,
    region: str,
    tags: dict[str, str] | None = None,
) -> dict:
    """Create the Lambda bridge function with a function URL.

    Returns dict with 'function_name', 'function_arn', 'function_url'.
    """
    lam = session.client("lambda")

    zip_bytes = _build_function_zip()

    # Create the function
    try:
        create_kwargs: dict = {
            "FunctionName": function_name,
            "Runtime": "python3.11",
            "Role": role_arn,
            "Handler": "index.handler",
            "Code": {"ZipFile": zip_bytes},
            "Timeout": 30,
            "MemorySize": 256,
            "Environment": {
                "Variables": {
                    "AGENT_RUNTIME_ARN": agent_runtime_arn,
                }
            },
        }
        if tags:
            create_kwargs["Tags"] = tags
        resp = lam.create_function(**create_kwargs)
        function_arn = resp["FunctionArn"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            # Function already exists — wait for any in-progress updates first
            _wait_for_lambda_active(lam, function_name)
            lam.update_function_code(
                FunctionName=function_name,
                ZipFile=zip_bytes,
            )
            _wait_for_lambda_active(lam, function_name)
            lam.update_function_configuration(
                FunctionName=function_name,
                Environment={
                    "Variables": {
                        "AGENT_RUNTIME_ARN": agent_runtime_arn,
                    }
                },
            )
            resp = lam.get_function(FunctionName=function_name)
            function_arn = resp["Configuration"]["FunctionArn"]
        else:
            raise

    # Wait for function to be active
    _wait_for_lambda_active(lam, function_name)

    # Create function URL
    function_url = _ensure_function_url(lam, function_name)

    return {
        "function_name": function_name,
        "function_arn": function_arn,
        "function_url": function_url,
    }


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
            raise RuntimeError(
                f"Lambda function {function_name} failed: {config.get('StateReason', 'unknown')}"
            )
        if last_update == "Failed":
            raise RuntimeError(
                f"Lambda function {function_name} update failed: "
                f"{config.get('LastUpdateStatusReason', 'unknown')}"
            )
        time.sleep(poll_interval)

    raise TimeoutError(f"Lambda {function_name} did not become Active within {max_wait_seconds}s")


def _ensure_function_url(lam, function_name: str) -> str:
    """Create or get the function URL for a Lambda function.

    Uses AuthType=AWS_IAM so only CloudFront (via OAC) can invoke it.
    """
    # Check if URL already exists
    try:
        resp = lam.get_function_url_config(FunctionName=function_name)
        return resp["FunctionUrl"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    # Create function URL with IAM auth (CloudFront OAC signs with SigV4)
    resp = lam.create_function_url_config(
        FunctionName=function_name,
        AuthType="AWS_IAM",
    )

    return resp["FunctionUrl"]


def grant_cloudfront_access(
    session: boto3.Session,
    function_name: str,
    distribution_arn: str,
) -> None:
    """Grant CloudFront OAC permission to invoke the Lambda function URL.

    Must be called after the CloudFront distribution is created (needs the ARN).
    """
    lam = session.client("lambda")
    try:
        lam.add_permission(
            FunctionName=function_name,
            StatementId="AllowCloudFrontOAC",
            Action="lambda:InvokeFunctionUrl",
            Principal="cloudfront.amazonaws.com",
            SourceArn=distribution_arn,
            FunctionUrlAuthType="AWS_IAM",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceConflictException":
            raise


def delete_lambda_function(session: boto3.Session, function_name: str) -> None:
    """Delete a Lambda function."""
    lam = session.client("lambda")
    try:
        lam.delete_function(FunctionName=function_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return
        raise


def delete_lambda_role(session: boto3.Session, role_name: str) -> None:
    """Delete the Lambda bridge IAM role and its inline policies."""
    iam = session.client("iam")
    try:
        policies = iam.list_role_policies(RoleName=role_name)
        for policy_name in policies.get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        iam.delete_role(RoleName=role_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            return
        raise


# --- Lambda@Edge for SHA256 computation (CloudFront OAC support) ---

_EDGE_FUNCTION_CODE = """\
'use strict';
const crypto = require('crypto');

exports.handler = async (event) => {
    const request = event.Records[0].cf.request;
    if (request.body && request.body.data) {
        const bodyData = Buffer.from(request.body.data, request.body.encoding);
        const hash = crypto.createHash('sha256').update(bodyData).digest('hex');
        request.headers['x-amz-content-sha256'] = [
            { key: 'x-amz-content-sha256', value: hash }
        ];
    }
    return request;
};
"""


def create_edge_role(
    session: boto3.Session,
    role_name: str,
    tags: list[dict[str, str]] | None = None,
) -> str:
    """Create an IAM role for the Lambda@Edge function.

    Lambda@Edge requires both lambda.amazonaws.com and edgelambda.amazonaws.com
    as trusted principals. Returns the role ARN.
    """
    iam = session.client("iam")

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

    try:
        create_kwargs: dict = {
            "RoleName": role_name,
            "AssumeRolePolicyDocument": json.dumps(trust_policy),
            "Description": "Execution role for three-stars Lambda@Edge SHA256 function",
        }
        if tags:
            create_kwargs["Tags"] = tags
        resp = iam.create_role(**create_kwargs)
        role_arn = resp["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            resp = iam.get_role(RoleName=role_name)
            role_arn = resp["Role"]["Arn"]
            if tags:
                iam.tag_role(RoleName=role_name, Tags=tags)
            return role_arn
        raise

    # Lambda@Edge only needs basic execution (CloudWatch logs)
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="lambda-edge-basic-execution",
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
                    }
                ],
            }
        ),
    )

    # Wait for role propagation
    time.sleep(10)

    return role_arn


def create_edge_function(
    session: boto3.Session,
    function_name: str,
    role_arn: str,
    tags: dict[str, str] | None = None,
) -> str:
    """Create a Lambda@Edge function in us-east-1 that computes SHA256 for OAC.

    Lambda@Edge must be deployed in us-east-1 regardless of project region.
    Returns the versioned function ARN (required for CloudFront association).
    """
    # Lambda@Edge must be in us-east-1
    lam = session.client("lambda", region_name="us-east-1")

    # Package the edge function
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.js", _EDGE_FUNCTION_CODE)
    zip_bytes = buffer.getvalue()

    try:
        create_kwargs: dict = {
            "FunctionName": function_name,
            "Runtime": "nodejs20.x",
            "Role": role_arn,
            "Handler": "index.handler",
            "Code": {"ZipFile": zip_bytes},
            "Timeout": 5,
            "MemorySize": 128,
            "Description": "Computes SHA256 for CloudFront OAC Lambda origin requests",
        }
        if tags:
            create_kwargs["Tags"] = tags
        lam.create_function(**create_kwargs)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            _wait_for_lambda_active(lam, function_name)
            lam.update_function_code(
                FunctionName=function_name,
                ZipFile=zip_bytes,
            )
        else:
            raise

    _wait_for_lambda_active(lam, function_name)

    # Publish a version (Lambda@Edge requires a specific version, not $LATEST)
    resp = lam.publish_version(
        FunctionName=function_name,
        Description="SHA256 edge function for CloudFront OAC",
    )

    return resp["FunctionArn"]


def delete_edge_function(session: boto3.Session, function_name: str) -> None:
    """Delete the Lambda@Edge function from us-east-1.

    Note: Replicas may take time to be removed after CloudFront dissociation.
    This may fail if replicas still exist — caller should handle retries.
    """
    lam = session.client("lambda", region_name="us-east-1")
    try:
        lam.delete_function(FunctionName=function_name)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            return
        if code == "InvalidParameterValueException" and "replicated" in str(e):
            # Replicas not yet removed — caller should retry later
            raise
        raise


def delete_edge_role(session: boto3.Session, role_name: str) -> None:
    """Delete the Lambda@Edge IAM role and its inline policies."""
    iam = session.client("iam")
    try:
        policies = iam.list_role_policies(RoleName=role_name)
        for policy_name in policies.get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        iam.delete_role(RoleName=role_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            return
        raise
