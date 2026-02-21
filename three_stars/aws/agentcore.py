"""Amazon Bedrock AgentCore runtime management.

Uses bedrock-agentcore-control (CRUD) and bedrock-agentcore (invocation).
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def package_agent(agent_dir: str | Path) -> bytes:
    """Package agent source directory into a zip file.

    Args:
        agent_dir: Path to the agent source directory.

    Returns:
        Zip file contents as bytes.
    """
    agent_path = Path(agent_dir)
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(agent_path.rglob("*")):
            if file_path.is_dir():
                continue
            # Skip __pycache__ and hidden files
            if "__pycache__" in file_path.parts or file_path.name.startswith("."):
                continue
            arcname = str(file_path.relative_to(agent_path))
            zf.write(file_path, arcname)

    return buffer.getvalue()


def upload_agent_package(
    session: boto3.Session,
    bucket_name: str,
    agent_zip: bytes,
    key: str = "agent.zip",
) -> tuple[str, str]:
    """Upload agent zip package to S3 staging bucket.

    Returns (bucket_name, key) tuple.
    """
    s3 = session.client("s3")
    s3.put_object(Bucket=bucket_name, Key=key, Body=agent_zip)
    return bucket_name, key


def create_iam_role(
    session: boto3.Session,
    role_name: str,
    account_id: str,
    tags: list[dict[str, str]] | None = None,
) -> str:
    """Create an IAM role for AgentCore runtime execution.

    Returns the role ARN.
    """
    iam = session.client("iam")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    try:
        create_kwargs: dict = {
            "RoleName": role_name,
            "AssumeRolePolicyDocument": json.dumps(trust_policy),
            "Description": "Execution role for three-stars AgentCore runtime",
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

    # Attach policy for Bedrock model invocation and S3 code access
    inline_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": f"arn:aws:bedrock:*:{account_id}:*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                ],
                "Resource": "arn:aws:s3:::sss-*/*",
            },
        ],
    }

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="three-stars-agentcore-policy",
        PolicyDocument=json.dumps(inline_policy),
    )

    # Wait for role propagation
    time.sleep(10)

    return role_arn


def create_agent_runtime(
    session: boto3.Session,
    name: str,
    s3_bucket: str,
    s3_key: str,
    role_arn: str,
    description: str = "",
    entry_point: list[str] | None = None,
    runtime: str = "PYTHON_3_11",
    environment_variables: dict[str, str] | None = None,
) -> dict:
    """Create a Bedrock AgentCore runtime.

    Args:
        session: boto3 session.
        name: Runtime name.
        s3_bucket: S3 bucket containing agent code.
        s3_key: S3 key of the agent zip package.
        role_arn: IAM execution role ARN.
        description: Runtime description.
        entry_point: Entry point command list.
        runtime: Python runtime version (PYTHON_3_11, PYTHON_3_12, etc).
        environment_variables: Environment variables for the runtime.

    Returns:
        Dict with 'runtime_id' and 'runtime_arn'.
    """
    client = session.client("bedrock-agentcore-control")

    if entry_point is None:
        entry_point = ["agent.py"]

    kwargs = {
        "agentRuntimeName": name,
        "description": description or f"three-stars runtime: {name}",
        "agentRuntimeArtifact": {
            "codeConfiguration": {
                "code": {
                    "s3": {
                        "bucket": s3_bucket,
                        "prefix": s3_key,
                    },
                },
                "runtime": runtime,
                "entryPoint": entry_point,
            },
        },
        "roleArn": role_arn,
        "networkConfiguration": {"networkMode": "PUBLIC"},
    }

    if environment_variables:
        kwargs["environmentVariables"] = environment_variables

    resp = client.create_agent_runtime(**kwargs)

    runtime_id = resp["agentRuntimeId"]
    runtime_arn = resp["agentRuntimeArn"]

    # Wait for runtime to become ready
    _wait_for_runtime_ready(client, runtime_id)

    return {
        "runtime_id": runtime_id,
        "runtime_arn": runtime_arn,
    }


def update_agent_runtime(
    session: boto3.Session,
    runtime_id: str,
    s3_bucket: str,
    s3_key: str,
    role_arn: str,
    description: str = "",
    entry_point: list[str] | None = None,
    runtime: str = "PYTHON_3_11",
    environment_variables: dict[str, str] | None = None,
) -> dict:
    """Update an existing AgentCore runtime with new code.

    Args:
        session: boto3 session.
        runtime_id: Existing runtime ID to update.
        s3_bucket: S3 bucket containing new agent code.
        s3_key: S3 key of the new agent zip package.
        role_arn: IAM execution role ARN.
        description: Runtime description.
        entry_point: Entry point command list.
        runtime: Python runtime version.
        environment_variables: Environment variables for the runtime.

    Returns:
        Dict with 'runtime_id' and 'runtime_arn'.
    """
    client = session.client("bedrock-agentcore-control")

    if entry_point is None:
        entry_point = ["agent.py"]

    kwargs: dict = {
        "agentRuntimeId": runtime_id,
        "agentRuntimeArtifact": {
            "codeConfiguration": {
                "code": {
                    "s3": {
                        "bucket": s3_bucket,
                        "prefix": s3_key,
                    },
                },
                "runtime": runtime,
                "entryPoint": entry_point,
            },
        },
    }

    if description:
        kwargs["description"] = description
    if role_arn:
        kwargs["roleArn"] = role_arn
    if environment_variables:
        kwargs["environmentVariables"] = environment_variables

    resp = client.update_agent_runtime(**kwargs)

    runtime_arn = resp["agentRuntimeArn"]

    # Wait for runtime to finish updating
    _wait_for_runtime_ready(client, runtime_id)

    return {
        "runtime_id": runtime_id,
        "runtime_arn": runtime_arn,
    }


def create_agent_runtime_endpoint(
    session: boto3.Session,
    runtime_id: str,
    endpoint_name: str,
) -> dict:
    """Create an endpoint for an AgentCore runtime.

    Args:
        session: boto3 session.
        runtime_id: The agent runtime ID.
        endpoint_name: Name for the endpoint.

    Returns:
        Dict with 'endpoint_name', 'endpoint_arn'.
    """
    client = session.client("bedrock-agentcore-control")

    resp = client.create_agent_runtime_endpoint(
        agentRuntimeId=runtime_id,
        name=endpoint_name,
    )

    endpoint_arn = resp["agentRuntimeEndpointArn"]

    # Wait for endpoint to become ready
    _wait_for_endpoint_ready(client, runtime_id, endpoint_name)

    return {
        "endpoint_name": endpoint_name,
        "endpoint_arn": endpoint_arn,
    }


def _wait_for_runtime_ready(
    client,
    runtime_id: str,
    max_wait_seconds: int = 300,
    poll_interval: int = 10,
) -> None:
    """Poll until runtime reaches READY status."""
    start = time.time()
    while time.time() - start < max_wait_seconds:
        resp = client.get_agent_runtime(agentRuntimeId=runtime_id)
        status = resp.get("status", "")
        if status == "READY":
            return
        if status in ("CREATE_FAILED", "UPDATE_FAILED"):
            raise RuntimeError(
                f"AgentCore runtime {runtime_id} entered {status} state: "
                f"{resp.get('failureReason', 'unknown reason')}"
            )
        time.sleep(poll_interval)

    raise TimeoutError(
        f"AgentCore runtime {runtime_id} did not reach READY status within {max_wait_seconds}s"
    )


def _wait_for_endpoint_ready(
    client,
    runtime_id: str,
    endpoint_name: str,
    max_wait_seconds: int = 300,
    poll_interval: int = 10,
) -> None:
    """Poll until endpoint reaches READY status."""
    start = time.time()
    while time.time() - start < max_wait_seconds:
        resp = client.get_agent_runtime_endpoint(
            agentRuntimeId=runtime_id,
            endpointName=endpoint_name,
        )
        status = resp.get("status", "")
        if status == "READY":
            return
        if status in ("CREATE_FAILED", "UPDATE_FAILED"):
            raise RuntimeError(
                f"AgentCore endpoint {endpoint_name} entered {status} state: "
                f"{resp.get('failureReason', 'unknown reason')}"
            )
        time.sleep(poll_interval)

    raise TimeoutError(
        f"AgentCore endpoint {endpoint_name} did not reach READY within {max_wait_seconds}s"
    )


def get_agent_runtime_status(session: boto3.Session, runtime_id: str) -> dict:
    """Get the current status of an AgentCore runtime."""
    client = session.client("bedrock-agentcore-control")
    resp = client.get_agent_runtime(agentRuntimeId=runtime_id)
    return {
        "runtime_id": runtime_id,
        "status": resp.get("status", "UNKNOWN"),
        "runtime_arn": resp.get("agentRuntimeArn", ""),
    }


def delete_agent_runtime_endpoint(
    session: boto3.Session,
    runtime_id: str,
    endpoint_name: str,
) -> None:
    """Delete an AgentCore runtime endpoint."""
    client = session.client("bedrock-agentcore-control")
    try:
        client.delete_agent_runtime_endpoint(
            agentRuntimeId=runtime_id,
            endpointName=endpoint_name,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return
        raise


def delete_agent_runtime(session: boto3.Session, runtime_id: str) -> None:
    """Delete an AgentCore runtime."""
    client = session.client("bedrock-agentcore-control")
    try:
        client.delete_agent_runtime(agentRuntimeId=runtime_id)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return
        raise


def delete_iam_role(session: boto3.Session, role_name: str) -> None:
    """Delete the IAM role and its inline policies."""
    iam = session.client("iam")
    try:
        # Remove inline policies first
        policies = iam.list_role_policies(RoleName=role_name)
        for policy_name in policies.get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        iam.delete_role(RoleName=role_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            return
        raise
