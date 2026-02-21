"""Deployment orchestration for three-stars."""

from __future__ import annotations

import hashlib

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from three_stars.aws import agentcore, cf_function, cloudfront, s3, session
from three_stars.config import ProjectConfig, get_resource_prefix, resolve_path
from three_stars.state import create_initial_state, load_state, save_state

console = Console()


def run_deploy(config: ProjectConfig, profile: str | None = None) -> dict:
    """Execute the full deployment workflow.

    Args:
        config: Validated project configuration.
        profile: AWS CLI profile name.

    Returns:
        Dict with deployment results including 'cloudfront_domain'.
    """
    sess = session.create_session(region=config.region, profile=profile)
    account_id = session.get_account_id(sess)
    prefix = get_resource_prefix(config)

    # Resource names
    bucket_name = f"{prefix}-{_short_hash(account_id)}"
    role_name = f"{prefix}-role"
    cf_function_name = f"{prefix}-router"
    agent_name = f"{prefix}-agent"

    # Load existing state or create new
    state = load_state(config.project_dir) or create_initial_state(config.name, config.region)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        # Step 1: IAM Role
        task = progress.add_task("Creating IAM role...", total=None)
        role_arn = agentcore.create_iam_role(sess, role_name, account_id)
        state["resources"]["iam_role_arn"] = role_arn
        state["resources"]["iam_role_name"] = role_name
        save_state(config.project_dir, state)
        progress.update(task, description="[green]IAM role ready")
        progress.remove_task(task)

        # Step 2: S3 Bucket
        task = progress.add_task("Creating S3 bucket...", total=None)
        s3.create_bucket(sess, bucket_name, config.region)
        state["resources"]["s3_bucket"] = bucket_name
        save_state(config.project_dir, state)
        progress.update(task, description="[green]S3 bucket ready")
        progress.remove_task(task)

        # Step 3: Upload frontend
        task = progress.add_task("Uploading frontend files...", total=None)
        app_path = resolve_path(config, config.app.source)
        file_count = s3.upload_directory(sess, bucket_name, app_path)
        progress.update(task, description=f"[green]Uploaded {file_count} files")
        progress.remove_task(task)

        # Step 4: Package and deploy agent
        task = progress.add_task("Packaging agent...", total=None)
        agent_path = resolve_path(config, config.agent.source)
        agent_zip = agentcore.package_agent(agent_path)
        agent_key = f"agents/{config.name}/agent.zip"
        agent_s3_uri = agentcore.upload_agent_package(sess, bucket_name, agent_zip, agent_key)
        progress.update(task, description="[green]Agent packaged")
        progress.remove_task(task)

        task = progress.add_task(
            "Deploying AgentCore runtime (this may take a few minutes)...", total=None
        )
        runtime = agentcore.create_agent_runtime(
            sess,
            name=agent_name,
            agent_s3_uri=agent_s3_uri,
            model_id=config.agent.model,
            role_arn=role_arn,
            description=config.agent.description,
            memory_mb=config.agent.memory,
        )
        state["resources"]["agentcore_runtime_id"] = runtime["runtime_id"]
        state["resources"]["agentcore_endpoint"] = runtime["endpoint"]
        save_state(config.project_dir, state)
        progress.update(task, description="[green]AgentCore runtime active")
        progress.remove_task(task)

        # Step 5: CloudFront Function
        task = progress.add_task("Creating CloudFront Function...", total=None)
        existing_cf_function = state["resources"].get("cloudfront_function_name")
        if existing_cf_function:
            function_arn = cf_function.update_function(
                sess, existing_cf_function, runtime["endpoint"], config.api.prefix
            )
        else:
            function_arn = cf_function.create_function(
                sess, cf_function_name, runtime["endpoint"], config.api.prefix
            )
        state["resources"]["cloudfront_function_name"] = cf_function_name
        state["resources"]["cloudfront_function_arn"] = function_arn
        save_state(config.project_dir, state)
        progress.update(task, description="[green]CloudFront Function ready")
        progress.remove_task(task)

        # Step 6: CloudFront Distribution
        existing_dist = state["resources"].get("cloudfront_distribution_id")
        if existing_dist:
            task = progress.add_task("CloudFront distribution already exists", total=None)
            dist_info = cloudfront.get_distribution(sess, existing_dist)
            progress.update(task, description="[green]CloudFront distribution exists")
            progress.remove_task(task)
        else:
            task = progress.add_task("Creating CloudFront distribution...", total=None)
            oac_id = cloudfront.create_origin_access_control(sess, f"{prefix}-oac")
            state["resources"]["oac_id"] = oac_id
            save_state(config.project_dir, state)

            dist_info = cloudfront.create_distribution(
                sess,
                bucket_name=bucket_name,
                region=config.region,
                oac_id=oac_id,
                function_arn=function_arn,
                index_document=config.app.index,
                api_prefix=config.api.prefix,
                comment=f"three-stars: {config.name}",
            )
            state["resources"]["cloudfront_distribution_id"] = dist_info["distribution_id"]
            state["resources"]["cloudfront_domain"] = dist_info["domain_name"]
            state["resources"]["cloudfront_arn"] = dist_info["arn"]
            save_state(config.project_dir, state)

            # Set S3 bucket policy for CloudFront access
            s3.set_bucket_policy_for_cloudfront(sess, bucket_name, dist_info["arn"])

            progress.update(
                task,
                description="[green]CloudFront distribution created (propagation ~5-10 min)",
            )
            progress.remove_task(task)

    return {
        "cloudfront_domain": state["resources"].get("cloudfront_domain", ""),
        "cloudfront_distribution_id": state["resources"].get("cloudfront_distribution_id", ""),
        "agentcore_runtime_id": state["resources"].get("agentcore_runtime_id", ""),
    }


def _short_hash(value: str) -> str:
    """Generate a short hash for resource name uniqueness."""
    return hashlib.sha256(value.encode()).hexdigest()[:8]
