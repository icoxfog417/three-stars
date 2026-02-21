"""Deployment orchestration for three-stars."""

from __future__ import annotations

import hashlib

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from three_stars.aws import agentcore, cloudfront, lambda_bridge, s3, session
from three_stars.config import (
    ProjectConfig,
    get_resource_prefix,
    get_resource_tags,
    resolve_path,
    tags_to_aws,
)
from three_stars.state import backup_state, create_initial_state, load_state, save_state

console = Console()

TOTAL_STEPS = 5


def _step_label(step: int, description: str) -> str:
    return f"[{step}/{TOTAL_STEPS}] {description}"


def run_deploy(
    config: ProjectConfig,
    profile: str | None = None,
    force: bool = False,
    verbose: bool = False,
) -> dict:
    """Execute the full deployment workflow.

    Args:
        config: Validated project configuration.
        profile: AWS CLI profile name.
        force: Force re-creation of all resources.
        verbose: Print detailed progress information.

    Returns:
        Dict with deployment results including 'cloudfront_domain'.
    """
    sess = session.create_session(region=config.region, profile=profile)
    account_id = session.get_account_id(sess)
    prefix = get_resource_prefix(config)
    tags = get_resource_tags(config)
    aws_tags = tags_to_aws(tags)

    # Resource names (AgentCore names must match [a-zA-Z][a-zA-Z0-9_]{0,47})
    bucket_name = f"{prefix}-{_short_hash(account_id)}"
    agentcore_role_name = f"{prefix}-role"
    lambda_role_name = f"{prefix}-lambda-role"
    lambda_function_name = f"{prefix}-api-bridge"
    edge_role_name = f"{prefix}-edge-role"
    edge_function_name = f"{prefix}-edge-sha256"
    ac_prefix = prefix.replace("-", "_")
    agent_name = f"{ac_prefix}_agent"
    endpoint_name = f"{ac_prefix}_endpoint"

    # Backup existing state before modifying
    backup_state(config.project_dir)

    # Load existing state or create new
    state = load_state(config.project_dir) or create_initial_state(config.name, config.region)
    is_update = bool(state.get("resources", {}).get("agentcore_runtime_id"))

    if is_update and not force:
        console.print("[dim]Existing deployment detected — updating resources.[/dim]")

    if force:
        console.print("[dim]Force mode — all resources will be re-created.[/dim]")
        state["resources"] = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        # Step 1: AgentCore (IAM role + runtime + endpoint)
        task = progress.add_task(_step_label(1, "Creating AgentCore resources..."), total=None)
        role_arn = agentcore.create_iam_role(sess, agentcore_role_name, account_id, tags=aws_tags)
        state["resources"]["iam_role_arn"] = role_arn
        state["resources"]["iam_role_name"] = agentcore_role_name
        save_state(config.project_dir, state)

        # Package and upload agent
        agent_path = resolve_path(config, config.agent.source)
        agent_zip = agentcore.package_agent(agent_path)
        agent_key = f"agents/{config.name}/agent.zip"

        # S3 bucket must exist for agent upload — create early if needed
        s3.create_bucket(sess, bucket_name, config.region)
        s3.tag_bucket(sess, bucket_name, aws_tags)
        state["resources"]["s3_bucket"] = bucket_name
        save_state(config.project_dir, state)

        agent_bucket, agent_s3_key = agentcore.upload_agent_package(
            sess, bucket_name, agent_zip, agent_key
        )

        existing_runtime_id = state["resources"].get("agentcore_runtime_id")
        if existing_runtime_id and not force:
            if verbose:
                console.print(f"  Updating AgentCore runtime {existing_runtime_id}...")
            runtime = agentcore.update_agent_runtime(
                sess,
                runtime_id=existing_runtime_id,
                s3_bucket=agent_bucket,
                s3_key=agent_s3_key,
                role_arn=role_arn,
                description=config.agent.description,
            )
            state["resources"]["agentcore_runtime_arn"] = runtime["runtime_arn"]
            save_state(config.project_dir, state)
            progress.update(task, description=_step_label(1, "[green]AgentCore updated"))
        else:
            runtime = agentcore.create_agent_runtime(
                sess,
                name=agent_name,
                s3_bucket=agent_bucket,
                s3_key=agent_s3_key,
                role_arn=role_arn,
                description=config.agent.description,
            )
            state["resources"]["agentcore_runtime_id"] = runtime["runtime_id"]
            state["resources"]["agentcore_runtime_arn"] = runtime["runtime_arn"]
            save_state(config.project_dir, state)

            # Create endpoint
            existing_endpoint = state["resources"].get("agentcore_endpoint_name")
            if not existing_endpoint:
                endpoint = agentcore.create_agent_runtime_endpoint(
                    sess, runtime["runtime_id"], endpoint_name
                )
                state["resources"]["agentcore_endpoint_name"] = endpoint["endpoint_name"]
                state["resources"]["agentcore_endpoint_arn"] = endpoint["endpoint_arn"]
                save_state(config.project_dir, state)
            progress.update(task, description=_step_label(1, "[green]AgentCore ready"))
        progress.remove_task(task)

        # Step 2: Storage (S3 bucket + frontend upload)
        task = progress.add_task(_step_label(2, "Uploading frontend files..."), total=None)
        app_path = resolve_path(config, config.app.source)
        file_count = s3.upload_directory(sess, bucket_name, app_path)
        progress.update(
            task, description=_step_label(2, f"[green]Uploaded {file_count} frontend files")
        )
        progress.remove_task(task)

        # Step 3: Lambda API bridge
        task = progress.add_task(_step_label(3, "Creating Lambda API bridge..."), total=None)
        lambda_role_arn = lambda_bridge.create_lambda_role(
            sess, lambda_role_name, account_id, config.region, tags=aws_tags
        )
        state["resources"]["lambda_role_name"] = lambda_role_name
        state["resources"]["lambda_role_arn"] = lambda_role_arn
        save_state(config.project_dir, state)

        lambda_info = lambda_bridge.create_lambda_function(
            sess,
            function_name=lambda_function_name,
            role_arn=lambda_role_arn,
            agent_runtime_arn=runtime["runtime_arn"],
            region=config.region,
            tags=tags,
        )
        state["resources"]["lambda_function_name"] = lambda_info["function_name"]
        state["resources"]["lambda_function_arn"] = lambda_info["function_arn"]
        state["resources"]["lambda_function_url"] = lambda_info["function_url"]
        save_state(config.project_dir, state)
        progress.update(task, description=_step_label(3, "[green]Lambda API bridge ready"))
        progress.remove_task(task)

        # Step 4: Lambda@Edge function (us-east-1)
        edge_function_arn = state["resources"].get("edge_function_arn")
        if not edge_function_arn or force:
            task = progress.add_task(
                _step_label(4, "Creating Lambda@Edge function (us-east-1)..."),
                total=None,
            )
            edge_role_arn = lambda_bridge.create_edge_role(sess, edge_role_name, tags=aws_tags)
            state["resources"]["edge_role_name"] = edge_role_name
            state["resources"]["edge_role_arn"] = edge_role_arn
            save_state(config.project_dir, state)

            edge_function_arn = lambda_bridge.create_edge_function(
                sess, edge_function_name, edge_role_arn, tags=tags
            )
            state["resources"]["edge_function_name"] = edge_function_name
            state["resources"]["edge_function_arn"] = edge_function_arn
            save_state(config.project_dir, state)
            progress.update(task, description=_step_label(4, "[green]Lambda@Edge function ready"))
            progress.remove_task(task)
        else:
            task = progress.add_task(
                _step_label(4, "[green]Lambda@Edge function exists"), total=None
            )
            progress.remove_task(task)

        # Step 5: CloudFront Distribution
        existing_dist = state["resources"].get("cloudfront_distribution_id")
        if existing_dist and not force:
            task = progress.add_task(
                _step_label(5, "[green]CloudFront distribution exists"), total=None
            )
            progress.remove_task(task)
        else:
            task = progress.add_task(
                _step_label(5, "Creating CloudFront distribution..."), total=None
            )
            oac_id = state["resources"].get("oac_id")
            if not oac_id:
                oac_id = cloudfront.create_origin_access_control(sess, f"{prefix}-oac")
                state["resources"]["oac_id"] = oac_id
                save_state(config.project_dir, state)

            lambda_oac_id = state["resources"].get("lambda_oac_id")
            if not lambda_oac_id:
                lambda_oac_id = cloudfront.create_origin_access_control(
                    sess, f"{prefix}-lambda-oac", origin_type="lambda"
                )
                state["resources"]["lambda_oac_id"] = lambda_oac_id
                save_state(config.project_dir, state)

            dist_info = cloudfront.create_distribution(
                sess,
                bucket_name=bucket_name,
                region=config.region,
                oac_id=oac_id,
                lambda_function_url=lambda_info["function_url"],
                lambda_oac_id=lambda_oac_id,
                edge_function_arn=edge_function_arn,
                index_document=config.app.index,
                api_prefix=config.api.prefix,
                comment=f"three-stars: {config.name}",
                tags=tags,
            )
            state["resources"]["cloudfront_distribution_id"] = dist_info["distribution_id"]
            state["resources"]["cloudfront_domain"] = dist_info["domain_name"]
            state["resources"]["cloudfront_arn"] = dist_info["arn"]
            save_state(config.project_dir, state)

            # Set S3 bucket policy for CloudFront access
            s3.set_bucket_policy_for_cloudfront(sess, bucket_name, dist_info["arn"])

            # Grant CloudFront OAC permission to invoke Lambda
            lambda_bridge.grant_cloudfront_access(sess, lambda_function_name, dist_info["arn"])

            progress.update(
                task,
                description=_step_label(
                    5, "[green]CloudFront distribution created (propagation ~5-10 min)"
                ),
            )
            progress.remove_task(task)

    # Post-deployment health check
    _print_health_check(sess, state)

    return {
        "cloudfront_domain": state["resources"].get("cloudfront_domain", ""),
        "cloudfront_distribution_id": state["resources"].get("cloudfront_distribution_id", ""),
        "agentcore_runtime_id": state["resources"].get("agentcore_runtime_id", ""),
    }


def _print_health_check(sess, state: dict) -> None:
    """Run a quick health check on deployed resources and print results."""
    resources = state.get("resources", {})
    table = Table(title="Post-Deployment Health Check")
    table.add_column("Resource", style="bold")
    table.add_column("ID / Name")
    table.add_column("Status")

    checks: list[tuple[str, str, str]] = []

    # S3
    bucket = resources.get("s3_bucket", "")
    if bucket:
        try:
            sess.client("s3").head_bucket(Bucket=bucket)
            checks.append(("S3 Bucket", bucket, "[green]Active[/green]"))
        except Exception:
            checks.append(("S3 Bucket", bucket, "[red]Not Found[/red]"))

    # Lambda
    func = resources.get("lambda_function_name", "")
    if func:
        try:
            resp = sess.client("lambda").get_function(FunctionName=func)
            fn_state = resp["Configuration"]["State"]
            if fn_state == "Active":
                checks.append(("Lambda Bridge", func, "[green]Active[/green]"))
            else:
                checks.append(("Lambda Bridge", func, f"[yellow]{fn_state}[/yellow]"))
        except Exception:
            checks.append(("Lambda Bridge", func, "[red]Not Found[/red]"))

    # CloudFront
    dist_id = resources.get("cloudfront_distribution_id", "")
    if dist_id:
        try:
            dist = cloudfront.get_distribution(sess, dist_id)
            status = dist["status"]
            if status == "Deployed":
                checks.append(("CloudFront", dist_id, "[green]Deployed[/green]"))
            else:
                checks.append(("CloudFront", dist_id, f"[yellow]{status}[/yellow]"))
        except Exception:
            checks.append(("CloudFront", dist_id, "[red]Not Found[/red]"))

    for resource, name, status in checks:
        table.add_row(resource, name, status)

    console.print()
    console.print(table)

    # Warn about CloudFront propagation
    if dist_id:
        dist_status = next((s for r, _, s in checks if r == "CloudFront"), "")
        if "yellow" in dist_status:
            domain = resources.get("cloudfront_domain", "")
            console.print(
                f"\n[yellow]CloudFront is still propagating.[/yellow] "
                f"Your site will be available at https://{domain} "
                f"in approximately 5-10 minutes."
            )


def _short_hash(value: str) -> str:
    """Generate a short hash for resource name uniqueness."""
    return hashlib.sha256(value.encode()).hexdigest()[:8]
