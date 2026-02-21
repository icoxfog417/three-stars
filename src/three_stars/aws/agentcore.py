"""Amazon Bedrock AgentCore runtime management.

Patterns adapted from bedrock-agentcore-starter-toolkit.
Uses direct boto3 calls for AgentCore runtime lifecycle.
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
) -> str:
    """Upload agent zip package to S3 staging bucket.

    Returns the S3 URI (s3://bucket/key).
    """
    s3 = session.client("s3")
    s3.put_object(Bucket=bucket_name, Key=key, Body=agent_zip)
    return f"s3://{bucket_name}/{key}"


def create_iam_role(
    session: boto3.Session,
    role_name: str,
    account_id: str,
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
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for three-stars AgentCore runtime",
        )
        role_arn = resp["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            resp = iam.get_role(RoleName=role_name)
            return resp["Role"]["Arn"]
        raise

    # Attach policy for Bedrock model invocation
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
                "Resource": "arn:aws:s3:::three-stars-*/*",
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
    agent_s3_uri: str,
    model_id: str,
    role_arn: str,
    description: str = "",
    memory_mb: int = 512,
) -> dict:
    """Create a Bedrock AgentCore runtime.

    Args:
        session: boto3 session.
        name: Runtime name.
        agent_s3_uri: S3 URI of the agent zip package.
        model_id: Bedrock model ID.
        role_arn: IAM execution role ARN.
        description: Runtime description.
        memory_mb: Memory allocation in MB.

    Returns:
        Dict with 'runtime_id' and 'endpoint'.
    """
    client = session.client("bedrock-agentcore")

    resp = client.create_agent_runtime(
        agentRuntimeName=name,
        description=description or f"three-stars runtime: {name}",
        agentRuntimeArtifact={
            "s3": {"s3BucketUri": agent_s3_uri},
        },
        roleArn=role_arn,
        networkConfiguration={"networkMode": "PUBLIC"},
    )

    runtime_id = resp["agentRuntimeId"]
    endpoint = resp.get("agentRuntimeEndpoint", "")

    # Wait for runtime to become active
    _wait_for_runtime_active(client, runtime_id)

    # Get endpoint after activation
    status_resp = client.get_agent_runtime(agentRuntimeId=runtime_id)
    endpoint = status_resp.get("agentRuntimeEndpoint", endpoint)

    return {
        "runtime_id": runtime_id,
        "endpoint": endpoint,
    }


def _wait_for_runtime_active(
    client,
    runtime_id: str,
    max_wait_seconds: int = 300,
    poll_interval: int = 10,
) -> None:
    """Poll until runtime reaches ACTIVE status."""
    start = time.time()
    while time.time() - start < max_wait_seconds:
        resp = client.get_agent_runtime(agentRuntimeId=runtime_id)
        status = resp.get("status", "")
        if status == "ACTIVE":
            return
        if status in ("FAILED", "DELETE_FAILED"):
            raise RuntimeError(
                f"AgentCore runtime {runtime_id} entered {status} state: "
                f"{resp.get('statusReason', 'unknown reason')}"
            )
        time.sleep(poll_interval)

    raise TimeoutError(
        f"AgentCore runtime {runtime_id} did not reach ACTIVE status within {max_wait_seconds}s"
    )


def get_agent_runtime_status(session: boto3.Session, runtime_id: str) -> dict:
    """Get the current status of an AgentCore runtime."""
    client = session.client("bedrock-agentcore")
    resp = client.get_agent_runtime(agentRuntimeId=runtime_id)
    return {
        "runtime_id": runtime_id,
        "status": resp.get("status", "UNKNOWN"),
        "endpoint": resp.get("agentRuntimeEndpoint", ""),
    }


def delete_agent_runtime(session: boto3.Session, runtime_id: str) -> None:
    """Delete an AgentCore runtime."""
    client = session.client("bedrock-agentcore")
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
