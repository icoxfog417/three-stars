"""AgentCore resource module — IAM role + runtime + endpoint + memory.

Uses bedrock-agentcore-starter-toolkit for code packaging, runtime CRUD,
and endpoint polling, with custom IAM logic.  Memory is managed via the
bedrock-agentcore SDK's MemoryClient.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore_starter_toolkit.services.runtime import BedrockAgentCoreClient
from bedrock_agentcore_starter_toolkit.utils.runtime.create_with_iam_eventual_consistency import (
    retry_create_with_eventual_iam_consistency,
)
from bedrock_agentcore_starter_toolkit.utils.runtime.package import CodeZipPackager
from botocore.exceptions import ClientError

from three_stars.config import ProjectConfig, resolve_path
from three_stars.naming import ResourceNames
from three_stars.resources import ResourceStatus
from three_stars.resources._base import AWSContext
from three_stars.state import AgentCoreState

_RUNTIME_VERSION = "PYTHON_3_11"


def deploy(
    ctx: AWSContext,
    config: ProjectConfig,
    names: ResourceNames,
    *,
    bucket_name: str,
    tags: list[dict[str, str]] | None = None,
    existing: AgentCoreState | None = None,
) -> AgentCoreState:
    """Create or update AgentCore resources.

    Args:
        ctx: AWS context.
        config: Project configuration.
        names: Computed resource names.
        bucket_name: S3 bucket for agent code upload.
        tags: AWS tags list [{"Key": k, "Value": v}].
        existing: Existing state if updating.

    Returns:
        AgentCoreState capturing all resource outputs.
    """
    role_arn = _create_iam_role(ctx, names.agentcore_role, ctx.account_id, tags=tags)

    # Package and upload agent code
    agent_path = resolve_path(config, config.agent.source)
    agent_key = f"agents/{config.name}/agent.zip"
    _package_and_upload(ctx, agent_path, names.agent_name, bucket_name, agent_key)

    # Create or reuse AgentCore Memory resource.
    # The SDK logs at ERROR level when the memory already exists (before the
    # create_or_get_memory fallback kicks in).  Suppress that expected noise.
    memory_client = MemoryClient(region_name=config.region)
    _memory_logger = logging.getLogger("bedrock_agentcore.memory")
    _prev_level = _memory_logger.level
    _memory_logger.setLevel(logging.CRITICAL)
    try:
        memory_info = memory_client.create_or_get_memory(
            name=names.memory,
            description=f"Conversation memory for {config.name}",
        )
    finally:
        _memory_logger.setLevel(_prev_level)
    memory_id = memory_info["id"]
    memory_name = memory_info.get("name", names.memory)

    # Set AWS_DEFAULT_REGION so agent code can create regional clients
    env_vars = {"AWS_DEFAULT_REGION": config.region, "MEMORY_ID": memory_id}
    env_vars.update(config.agent.env_vars)

    # Determine whether this is a create or update
    agent_id = existing.runtime_id if existing and not _is_empty_state(existing) else None

    toolkit_client = BedrockAgentCoreClient(region=config.region)

    agent_info = retry_create_with_eventual_iam_consistency(
        create_function=lambda: toolkit_client.create_or_update_agent(
            agent_id=agent_id,
            agent_name=names.agent_name,
            execution_role_arn=role_arn,
            deployment_type="direct_code_deploy",
            code_s3_bucket=bucket_name,
            code_s3_key=agent_key,
            runtime_type=_RUNTIME_VERSION,
            entrypoint_array=["opentelemetry-instrument", "agent.py"],
            network_config={"networkMode": "PUBLIC"},
            env_vars=env_vars,
            auto_update_on_conflict=True,
        ),
        execution_role_arn=role_arn,
    )

    runtime_id = agent_info["id"]
    runtime_arn = agent_info["arn"]

    # Wait for the DEFAULT endpoint to become ready (auto-created with runtime)
    endpoint_arn = toolkit_client.wait_for_agent_endpoint_ready(
        agent_id=runtime_id,
        endpoint_name="DEFAULT",
        max_wait=300,
    )

    # The toolkit returns a non-ARN string on timeout
    if not isinstance(endpoint_arn, str) or not endpoint_arn.startswith("arn:"):
        raise TimeoutError(
            f"AgentCore endpoint for runtime {runtime_id} did not reach READY "
            f"within 300s: {endpoint_arn}"
        )

    return AgentCoreState(
        iam_role_name=names.agentcore_role,
        iam_role_arn=role_arn,
        runtime_id=runtime_id,
        runtime_arn=runtime_arn,
        endpoint_name="DEFAULT",
        endpoint_arn=endpoint_arn,
        memory_id=memory_id,
        memory_name=memory_name,
    )


def set_resource_policy(
    ctx: AWSContext,
    *,
    runtime_arn: str,
    edge_role_arn: str,
) -> None:
    """Attach a resource-based policy to restrict AgentCore invocation.

    Only the Lambda@Edge IAM role is allowed to invoke the runtime.
    """
    client = ctx.client("bedrock-agentcore-control")
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": edge_role_arn},
                "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": runtime_arn,
            }
        ],
    }
    client.put_resource_policy(
        resourceArn=runtime_arn,
        policy=json.dumps(policy),
    )


def destroy(ctx: AWSContext, state: AgentCoreState) -> None:
    """Delete AgentCore resources."""
    client = ctx.client("bedrock-agentcore-control")

    # Delete Memory resource (if present)
    if state.memory_id:
        try:
            memory_client = MemoryClient(region_name=ctx.session.region_name)
            memory_client.delete_memory_and_wait(memory_id=state.memory_id)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise

    # Legacy deployments may have a non-DEFAULT endpoint; delete it explicitly.
    # For DEFAULT endpoints, deleting the runtime auto-deletes the endpoint.
    if state.endpoint_name and state.endpoint_name != "DEFAULT":
        try:
            client.delete_agent_runtime_endpoint(
                agentRuntimeId=state.runtime_id,
                endpointName=state.endpoint_name,
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise

    # Delete runtime (DEFAULT endpoint is auto-deleted)
    try:
        client.delete_agent_runtime(agentRuntimeId=state.runtime_id)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    # Delete IAM role
    _delete_iam_role(ctx, state.iam_role_name)


def get_status(ctx: AWSContext, state: AgentCoreState) -> list[ResourceStatus]:
    """Return status rows for AgentCore resources."""
    rows: list[ResourceStatus] = []

    # Runtime status
    name = "AgentCore Runtime"
    rid = state.runtime_id
    try:
        client = ctx.client("bedrock-agentcore-control")
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

    # Memory
    if state.memory_id:
        mem_name = "AgentCore Memory"
        try:
            memory_client = MemoryClient(region_name=ctx.session.region_name)
            mem_status = memory_client.get_memory_status(memory_id=state.memory_id)
            if mem_status == "ACTIVE":
                rows.append(ResourceStatus(mem_name, state.memory_id, "[green]Active[/green]"))
            elif mem_status in ("CREATING", "UPDATING"):
                rows.append(
                    ResourceStatus(mem_name, state.memory_id, f"[yellow]{mem_status}[/yellow]")
                )
            else:
                rows.append(ResourceStatus(mem_name, state.memory_id, f"[red]{mem_status}[/red]"))
        except Exception:
            rows.append(ResourceStatus(mem_name, state.memory_id, "[red]Not Found[/red]"))

    # IAM role
    rows.append(ResourceStatus("AgentCore IAM Role", state.iam_role_name, "[green]Active[/green]"))

    return rows


def _is_empty_state(state: AgentCoreState) -> bool:
    return not state.runtime_id


def _package_and_upload(
    ctx: AWSContext,
    agent_path: Path,
    agent_name: str,
    bucket_name: str,
    key: str,
) -> None:
    """Package agent code with toolkit's CodeZipPackager and upload to S3."""
    packager = CodeZipPackager()
    cache_dir = agent_path / ".bedrock_agentcore" / agent_name
    cache_dir.mkdir(parents=True, exist_ok=True)

    requirements = agent_path / "requirements.txt"

    deployment_zip, _ = packager.create_deployment_package(
        source_dir=agent_path,
        agent_name=agent_name,
        cache_dir=cache_dir,
        runtime_version=_RUNTIME_VERSION,
        requirements_file=requirements if requirements.exists() else None,
    )

    s3 = ctx.client("s3")
    s3.upload_file(str(deployment_zip), bucket_name, key)


def _create_iam_role(
    ctx: AWSContext,
    role_name: str,
    account_id: str,
    tags: list[dict[str, str]] | None = None,
) -> str:
    """Create an IAM role for AgentCore runtime execution. Returns the role ARN."""
    iam = ctx.client("iam")

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
                "Resource": [
                    f"arn:aws:bedrock:*:{account_id}:*",
                    "arn:aws:bedrock:*::foundation-model/*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": "arn:aws:s3:::sss-*/*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:DeleteEvent",
                    "bedrock-agentcore:ListSessions",
                    "bedrock-agentcore:GetMemory",
                    "bedrock-agentcore:RetrieveMemories",
                ],
                "Resource": f"arn:aws:bedrock-agentcore:*:{account_id}:memory/*",
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


def _delete_iam_role(ctx: AWSContext, role_name: str) -> None:
    """Delete the IAM role and its inline policies."""
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
