"""Deployment status reporting and AWS resource discovery for three-stars."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError
from rich.console import Console
from rich.table import Table

from three_stars.resources import agentcore, cdn, edge, storage
from three_stars.resources._base import AWSContext
from three_stars.state import (
    STATE_VERSION,
    AgentCoreState,
    CdnState,
    DeploymentState,
    EdgeState,
    StorageState,
    load_state,
    save_state,
)

if TYPE_CHECKING:
    from three_stars.config import ProjectConfig

console = Console()


# ── Public API ──────────────────────────────────────────────────────────


def run_status(
    project_dir: str,
    profile: str | None = None,
    *,
    sync: bool = False,
    config: ProjectConfig | None = None,
) -> None:
    """Display deployment status for a three-stars project.

    Args:
        project_dir: Path to the project directory.
        profile: AWS CLI profile name.
        sync: If True, discover actual state from AWS before showing status.
        config: Project config (required when sync=True and no state file exists).
    """
    if sync:
        state = _run_sync(project_dir, profile=profile, config=config)
    else:
        state = load_state(project_dir)

    if state is None:
        console.print("[yellow]No deployment found.[/yellow]")
        console.print("Run 'sss deploy' to deploy your project.")
        if not sync:
            console.print("[dim]Tip: use --sync to discover resources from AWS.[/dim]")
        return

    console.print(f"\n[bold]Project:[/bold] [cyan]{state.project_name}[/cyan]")
    console.print(f"[bold]Region:[/bold] [yellow]{state.region}[/yellow]")
    console.print(f"[bold]Deployed at:[/bold] {state.deployed_at}")

    # Create status table
    table = Table(title="Resource Status")
    table.add_column("Resource", style="bold")
    table.add_column("ID / Name")
    table.add_column("Status")

    ctx = AWSContext.create(region=state.region, profile=profile)

    # Collect status rows from each resource module
    if state.storage:
        for row in storage.get_status(ctx, state.storage):
            table.add_row(row.resource, row.id, row.status)

    if state.agentcore:
        for row in agentcore.get_status(ctx, state.agentcore):
            table.add_row(row.resource, row.id, row.status)

    if state.edge:
        for row in edge.get_status(ctx, state.edge):
            table.add_row(row.resource, row.id, row.status)

    if state.cdn:
        for row in cdn.get_status(ctx, state.cdn):
            table.add_row(row.resource, row.id, row.status)

    console.print()
    console.print(table)

    # Print URL
    if state.cdn:
        console.print(f"\n[bold]URL:[/bold] https://{state.cdn.domain}")


def refresh_state(
    ctx: AWSContext,
    config: ProjectConfig,
    project_dir: str | Path,
) -> DeploymentState | None:
    """Discover actual AWS resource state and update the local state file.

    Queries AWS for all resources by their computed names, builds a
    ``DeploymentState``, and saves it to disk.

    Returns:
        A ``DeploymentState`` if any resources are found, otherwise ``None``.
    """
    state = _discover_state(ctx, config)
    if state is not None:
        save_state(project_dir, state)
    return state


# ── Internal helpers ────────────────────────────────────────────────────


def _run_sync(
    project_dir: str,
    profile: str | None = None,
    config: ProjectConfig | None = None,
) -> DeploymentState | None:
    """Sync path: discover from AWS, update state file, return state."""
    if config is None:
        from three_stars.config import load_config

        config = load_config(project_dir)

    ctx = AWSContext.create(region=config.region, profile=profile)
    console.print(f"[dim]Syncing state from AWS for project '{config.name}'...[/dim]")
    return refresh_state(ctx, config, project_dir)


def _discover_state(
    ctx: AWSContext,
    config: ProjectConfig,
) -> DeploymentState | None:
    """Discover deployment state by querying AWS for resources by computed name.

    All resource names are deterministic (via ``compute_names``), so each
    resource is looked up by its expected name.  AgentCore runtime and
    CloudFront distribution IDs are AWS-assigned, so they require listing
    and matching by name / origin.

    Returns:
        A ``DeploymentState`` if any resources are found, otherwise ``None``.
    """
    from three_stars.naming import compute_names

    names = compute_names(config, ctx.account_id)

    storage_state = _discover_storage(ctx, names.bucket)
    edge_state = _discover_edge(ctx, names.edge_function, names.edge_role)
    cdn_state = _discover_cdn(ctx, names.bucket)
    agentcore_state = _discover_agentcore(
        ctx,
        names.agentcore_role,
        names.agent_name,
        config.region,
    )

    if not any([storage_state, edge_state, cdn_state, agentcore_state]):
        return None

    return DeploymentState(
        version=STATE_VERSION,
        project_name=config.name,
        region=config.region,
        deployed_at="",
        agentcore=agentcore_state,
        storage=storage_state,
        edge=edge_state,
        cdn=cdn_state,
    )


def _discover_storage(ctx: AWSContext, bucket_name: str) -> StorageState | None:
    """Check if the expected S3 bucket exists."""
    try:
        ctx.client("s3").head_bucket(Bucket=bucket_name)
        return StorageState(s3_bucket=bucket_name)
    except ClientError:
        return None


def _discover_edge(
    ctx: AWSContext,
    function_name: str,
    role_name: str,
) -> EdgeState | None:
    """Check if the expected Lambda@Edge function exists."""
    try:
        lam = ctx.client("lambda", region_name="us-east-1")
        resp = lam.get_function(FunctionName=function_name)
        function_arn = resp["Configuration"]["FunctionArn"]
    except ClientError:
        return None

    try:
        iam = ctx.client("iam")
        role_resp = iam.get_role(RoleName=role_name)
        role_arn = role_resp["Role"]["Arn"]
    except ClientError:
        role_arn = ""

    return EdgeState(
        role_name=role_name,
        role_arn=role_arn,
        function_name=function_name,
        function_arn=function_arn,
    )


def _discover_cdn(ctx: AWSContext, bucket_name: str) -> CdnState | None:
    """Find a CloudFront distribution by its S3 origin.

    Distributions created by three-stars use origin ID ``S3-{bucket_name}``.
    """
    expected_origin_id = f"S3-{bucket_name}"
    try:
        cf = ctx.client("cloudfront")
        paginator = cf.get_paginator("list_distributions")
        for page in paginator.paginate():
            dist_list = page.get("DistributionList", {})
            for dist in dist_list.get("Items", []):
                for origin in dist.get("Origins", {}).get("Items", []):
                    if origin.get("Id") == expected_origin_id:
                        oac_id = origin.get("OriginAccessControlId", "")
                        return CdnState(
                            distribution_id=dist["Id"],
                            domain=dist["DomainName"],
                            arn=dist["ARN"],
                            oac_id=oac_id,
                            lambda_oac_id="",
                        )
    except ClientError:
        pass
    return None


def _discover_agentcore(
    ctx: AWSContext,
    role_name: str,
    agent_name: str,
    region: str,
) -> AgentCoreState | None:
    """Find the AgentCore runtime by agent name."""
    try:
        iam = ctx.client("iam")
        role_resp = iam.get_role(RoleName=role_name)
        role_arn = role_resp["Role"]["Arn"]
    except ClientError:
        role_arn = ""

    try:
        client = ctx.client("bedrock-agentcore-control", region_name=region)
        paginator = client.get_paginator("list_agent_runtimes")
        for page in paginator.paginate():
            for runtime in page.get("agentRuntimeSummaries", []):
                if runtime.get("agentRuntimeName") == agent_name:
                    return AgentCoreState(
                        iam_role_name=role_name,
                        iam_role_arn=role_arn,
                        runtime_id=runtime["agentRuntimeId"],
                        runtime_arn=runtime["agentRuntimeArn"],
                        endpoint_name="DEFAULT",
                        endpoint_arn="",
                    )
    except ClientError:
        pass

    # No runtime found — if role exists, still return partial state for cleanup
    if role_arn:
        return AgentCoreState(
            iam_role_name=role_name,
            iam_role_arn=role_arn,
            runtime_id="",
            runtime_arn="",
            endpoint_name="",
            endpoint_arn="",
        )

    return None
