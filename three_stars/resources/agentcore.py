"""AgentCore resource module — IAM role + runtime + endpoint.

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

from three_stars.config import ProjectConfig, resolve_path
from three_stars.naming import ResourceNames
from three_stars.resources import ResourceStatus
from three_stars.state import AgentCoreState


def deploy(
    session: boto3.Session,
    config: ProjectConfig,
    names: ResourceNames,
    *,
    bucket_name: str,
    tags: list[dict[str, str]] | None = None,
    existing: AgentCoreState | None = None,
) -> AgentCoreState:
    """Create or update AgentCore resources.

    Args:
        session: boto3 session.
        config: Project configuration.
        names: Computed resource names.
        bucket_name: S3 bucket for agent code upload.
        tags: AWS tags list [{"Key": k, "Value": v}].
        existing: Existing state if updating.

    Returns:
        AgentCoreState capturing all resource outputs.
    """
    account_id = session.client("sts").get_caller_identity()["Account"]
    role_arn = _create_iam_role(session, names.agentcore_role, account_id, tags=tags)

    # Package and upload agent code
    agent_path = resolve_path(config, config.agent.source)
    agent_zip = _package_agent(agent_path)
    agent_key = f"agents/{config.name}/agent.zip"
    _upload_agent_package(session, bucket_name, agent_zip, agent_key)

    if existing and not _is_empty_state(existing):
        # Update existing runtime
        runtime = _update_agent_runtime(
            session,
            runtime_id=existing.runtime_id,
            s3_bucket=bucket_name,
            s3_key=agent_key,
            role_arn=role_arn,
            description=config.agent.description,
        )
        return AgentCoreState(
            iam_role_name=names.agentcore_role,
            iam_role_arn=role_arn,
            runtime_id=existing.runtime_id,
            runtime_arn=runtime["runtime_arn"],
            endpoint_name=existing.endpoint_name,
            endpoint_arn=existing.endpoint_arn,
        )

    # Create new runtime
    runtime = _create_agent_runtime(
        session,
        name=names.agent_name,
        s3_bucket=bucket_name,
        s3_key=agent_key,
        role_arn=role_arn,
        description=config.agent.description,
    )

    # Create endpoint
    endpoint = _create_agent_runtime_endpoint(session, runtime["runtime_id"], names.endpoint_name)

    return AgentCoreState(
        iam_role_name=names.agentcore_role,
        iam_role_arn=role_arn,
        runtime_id=runtime["runtime_id"],
        runtime_arn=runtime["runtime_arn"],
        endpoint_name=endpoint["endpoint_name"],
        endpoint_arn=endpoint["endpoint_arn"],
    )


def destroy(session: boto3.Session, state: AgentCoreState) -> None:
    """Delete AgentCore resources."""
    client = session.client("bedrock-agentcore-control")

    # Delete endpoint
    try:
        client.delete_agent_runtime_endpoint(
            agentRuntimeId=state.runtime_id,
            endpointName=state.endpoint_name,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    # Delete runtime
    try:
        client.delete_agent_runtime(agentRuntimeId=state.runtime_id)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    # Delete IAM role
    _delete_iam_role(session, state.iam_role_name)


def get_status(session: boto3.Session, state: AgentCoreState) -> list[ResourceStatus]:
    """Return status rows for AgentCore resources."""
    rows: list[ResourceStatus] = []

    # Runtime status
    name = "AgentCore Runtime"
    rid = state.runtime_id
    try:
        client = session.client("bedrock-agentcore-control")
        resp = client.get_agent_runtime(agentRuntimeId=rid)
        status = resp.get("status", "UNKNOWN")
        if status == "READY":
            rows.append(ResourceStatus(name, rid, "[green]Ready[/green]"))
        elif status in ("CREATING", "UPDATING"):
            rows.append(ResourceStatus(name, rid, f"[yellow]{status}[/yellow]"))
        else:
            rows.append(ResourceStatus(name, rid, f"[red]{status}[/red]"))
    except Exception:
        rows.append(ResourceStatus(name, rid, "[red]Not Found[/red]"))

    # Endpoint
    rows.append(ResourceStatus("AgentCore Endpoint", state.endpoint_name, rows[0].status))

    # IAM role
    rows.append(ResourceStatus("AgentCore IAM Role", state.iam_role_name, "[green]Active[/green]"))

    return rows


def _is_empty_state(state: AgentCoreState) -> bool:
    return not state.runtime_id


def _package_agent(agent_dir: str | Path) -> bytes:
    """Package agent source directory into a zip file."""
    agent_path = Path(agent_dir)
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(agent_path.rglob("*")):
            if file_path.is_dir():
                continue
            if "__pycache__" in file_path.parts or file_path.name.startswith("."):
                continue
            arcname = str(file_path.relative_to(agent_path))
            zf.write(file_path, arcname)

    return buffer.getvalue()


def _upload_agent_package(
    session: boto3.Session,
    bucket_name: str,
    agent_zip: bytes,
    key: str = "agent.zip",
) -> tuple[str, str]:
    """Upload agent zip package to S3 staging bucket."""
    s3 = session.client("s3")
    s3.put_object(Bucket=bucket_name, Key=key, Body=agent_zip)
    return bucket_name, key


def _create_iam_role(
    session: boto3.Session,
    role_name: str,
    account_id: str,
    tags: list[dict[str, str]] | None = None,
) -> str:
    """Create an IAM role for AgentCore runtime execution. Returns the role ARN."""
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

    created = False
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
        created = True
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            resp = iam.get_role(RoleName=role_name)
            role_arn = resp["Role"]["Arn"]
            if tags:
                iam.tag_role(RoleName=role_name, Tags=tags)
        else:
            raise

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
                "Action": ["s3:GetObject"],
                "Resource": "arn:aws:s3:::sss-*/*",
            },
        ],
    }

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="three-stars-agentcore-policy",
        PolicyDocument=json.dumps(inline_policy),
    )

    if created:
        time.sleep(10)
    return role_arn


def _create_agent_runtime(
    session: boto3.Session,
    name: str,
    s3_bucket: str,
    s3_key: str,
    role_arn: str,
    description: str = "",
    entry_point: list[str] | None = None,
    runtime: str = "PYTHON_3_11",
) -> dict:
    """Create a Bedrock AgentCore runtime."""
    client = session.client("bedrock-agentcore-control")

    if entry_point is None:
        entry_point = ["agent.py"]

    kwargs = {
        "agentRuntimeName": name,
        "description": description or f"three-stars runtime: {name}",
        "agentRuntimeArtifact": {
            "codeConfiguration": {
                "code": {"s3": {"bucket": s3_bucket, "prefix": s3_key}},
                "runtime": runtime,
                "entryPoint": entry_point,
            },
        },
        "roleArn": role_arn,
        "networkConfiguration": {"networkMode": "PUBLIC"},
    }

    resp = client.create_agent_runtime(**kwargs)
    runtime_id = resp["agentRuntimeId"]
    runtime_arn = resp["agentRuntimeArn"]

    _wait_for_runtime_ready(client, runtime_id)

    return {"runtime_id": runtime_id, "runtime_arn": runtime_arn}


def _update_agent_runtime(
    session: boto3.Session,
    runtime_id: str,
    s3_bucket: str,
    s3_key: str,
    role_arn: str,
    description: str = "",
    entry_point: list[str] | None = None,
    runtime: str = "PYTHON_3_11",
) -> dict:
    """Update an existing AgentCore runtime with new code."""
    client = session.client("bedrock-agentcore-control")

    if entry_point is None:
        entry_point = ["agent.py"]

    kwargs: dict = {
        "agentRuntimeId": runtime_id,
        "agentRuntimeArtifact": {
            "codeConfiguration": {
                "code": {"s3": {"bucket": s3_bucket, "prefix": s3_key}},
                "runtime": runtime,
                "entryPoint": entry_point,
            },
        },
    }

    if description:
        kwargs["description"] = description
    if role_arn:
        kwargs["roleArn"] = role_arn

    resp = client.update_agent_runtime(**kwargs)
    runtime_arn = resp["agentRuntimeArn"]

    _wait_for_runtime_ready(client, runtime_id)

    return {"runtime_id": runtime_id, "runtime_arn": runtime_arn}


def _create_agent_runtime_endpoint(
    session: boto3.Session,
    runtime_id: str,
    endpoint_name: str,
) -> dict:
    """Create an endpoint for an AgentCore runtime."""
    client = session.client("bedrock-agentcore-control")

    resp = client.create_agent_runtime_endpoint(
        agentRuntimeId=runtime_id,
        name=endpoint_name,
    )
    endpoint_arn = resp["agentRuntimeEndpointArn"]

    _wait_for_endpoint_ready(client, runtime_id, endpoint_name)

    return {"endpoint_name": endpoint_name, "endpoint_arn": endpoint_arn}


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


def _delete_iam_role(session: boto3.Session, role_name: str) -> None:
    """Delete the IAM role and its inline policies."""
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
