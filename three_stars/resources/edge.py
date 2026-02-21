"""Edge resource module — Lambda@Edge function + IAM role (us-east-1)."""

from __future__ import annotations

import io
import json
import time
import zipfile

import boto3
from botocore.exceptions import ClientError

from three_stars.naming import ResourceNames
from three_stars.resources import ResourceStatus
from three_stars.state import EdgeState

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


def deploy(
    session: boto3.Session,
    names: ResourceNames,
    *,
    tags: list[dict[str, str]] | None = None,
    tags_dict: dict[str, str] | None = None,
    existing: EdgeState | None = None,
) -> EdgeState:
    """Create Lambda@Edge function + IAM role in us-east-1.

    Args:
        tags: AWS tag list format for IAM roles.
        tags_dict: Dict format tags for Lambda functions.
        existing: Existing state if updating (skips creation).
    """
    if existing:
        return existing

    role_arn = _create_edge_role(session, names.edge_role, tags=tags)

    function_arn = _create_edge_function(session, names.edge_function, role_arn, tags=tags_dict)

    return EdgeState(
        role_name=names.edge_role,
        role_arn=role_arn,
        function_name=names.edge_function,
        function_arn=function_arn,
    )


def destroy(session: boto3.Session, state: EdgeState) -> None:
    """Delete Lambda@Edge function and IAM role."""
    _delete_edge_function(session, state.function_name)
    _delete_edge_role(session, state.role_name)


def get_status(session: boto3.Session, state: EdgeState) -> list[ResourceStatus]:
    """Return Lambda@Edge status."""
    rows: list[ResourceStatus] = []

    name = "Lambda@Edge"
    fn = state.function_name
    try:
        lam = session.client("lambda", region_name="us-east-1")
        resp = lam.get_function(FunctionName=fn)
        fn_state = resp["Configuration"]["State"]
        if fn_state == "Active":
            rows.append(ResourceStatus(name, fn, "[green]Active[/green]"))
        else:
            rows.append(ResourceStatus(name, fn, f"[yellow]{fn_state}[/yellow]"))
    except Exception:
        rows.append(ResourceStatus(name, fn, "[red]Not Found[/red]"))

    return rows


def _create_edge_role(
    session: boto3.Session,
    role_name: str,
    tags: list[dict[str, str]] | None = None,
) -> str:
    """Create an IAM role for the Lambda@Edge function. Returns the role ARN."""
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

    time.sleep(10)
    return role_arn


def _create_edge_function(
    session: boto3.Session,
    function_name: str,
    role_arn: str,
    tags: dict[str, str] | None = None,
) -> str:
    """Create a Lambda@Edge function in us-east-1. Returns versioned ARN."""
    lam = session.client("lambda", region_name="us-east-1")

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
            lam.update_function_code(FunctionName=function_name, ZipFile=zip_bytes)
        else:
            raise

    _wait_for_lambda_active(lam, function_name)

    resp = lam.publish_version(
        FunctionName=function_name,
        Description="SHA256 edge function for CloudFront OAC",
    )

    return resp["FunctionArn"]


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


def _delete_edge_function(session: boto3.Session, function_name: str) -> None:
    """Delete the Lambda@Edge function from us-east-1."""
    lam = session.client("lambda", region_name="us-east-1")
    try:
        lam.delete_function(FunctionName=function_name)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            return
        if code == "InvalidParameterValueException" and "replicated" in str(e):
            raise
        raise


def _delete_edge_role(session: boto3.Session, role_name: str) -> None:
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
