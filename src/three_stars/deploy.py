"""Deployment orchestration for three-stars."""

from __future__ import annotations

import hashlib

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from three_stars.aws import agentcore, cloudfront, lambda_bridge, s3, session
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

    # Resource names (AgentCore names must match [a-zA-Z][a-zA-Z0-9_]{0,47})
    bucket_name = f"{prefix}-{_short_hash(account_id)}"
    agentcore_role_name = f"{prefix}-role"
    lambda_role_name = f"{prefix}-lambda-role"
    lambda_function_name = f"{prefix}-api-bridge"
    ac_prefix = prefix.replace("-", "_")
    agent_name = f"{ac_prefix}_agent"
    endpoint_name = f"{ac_prefix}_endpoint"

    # Load existing state or create new
    state = load_state(config.project_dir) or create_initial_state(config.name, config.region)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        # Step 1: IAM Role for AgentCore
        task = progress.add_task("Creating AgentCore IAM role...", total=None)
        role_arn = agentcore.create_iam_role(sess, agentcore_role_name, account_id)
        state["resources"]["iam_role_arn"] = role_arn
        state["resources"]["iam_role_name"] = agentcore_role_name
        save_state(config.project_dir, state)
        progress.update(task, description="[green]AgentCore IAM role ready")
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
        agent_bucket, agent_s3_key = agentcore.upload_agent_package(
            sess, bucket_name, agent_zip, agent_key
        )
        progress.update(task, description="[green]Agent packaged")
        progress.remove_task(task)

        existing_runtime_id = state["resources"].get("agentcore_runtime_id")
        if existing_runtime_id:
            task = progress.add_task("AgentCore runtime already exists", total=None)
            runtime = {
                "runtime_id": existing_runtime_id,
                "runtime_arn": state["resources"].get("agentcore_runtime_arn", ""),
            }
            progress.update(task, description="[green]AgentCore runtime exists")
            progress.remove_task(task)
        else:
            task = progress.add_task(
                "Deploying AgentCore runtime (this may take a few minutes)...", total=None
            )
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
            progress.update(task, description="[green]AgentCore runtime ready")
            progress.remove_task(task)

        # Step 5: Create AgentCore endpoint
        existing_endpoint = state["resources"].get("agentcore_endpoint_name")
        if existing_endpoint:
            task = progress.add_task("AgentCore endpoint already exists", total=None)
            progress.update(task, description="[green]AgentCore endpoint exists")
            progress.remove_task(task)
        else:
            task = progress.add_task("Creating AgentCore endpoint...", total=None)
            endpoint = agentcore.create_agent_runtime_endpoint(
                sess, runtime["runtime_id"], endpoint_name
            )
            state["resources"]["agentcore_endpoint_name"] = endpoint["endpoint_name"]
            state["resources"]["agentcore_endpoint_arn"] = endpoint["endpoint_arn"]
            save_state(config.project_dir, state)
            progress.update(task, description="[green]AgentCore endpoint ready")
            progress.remove_task(task)

        # Step 6: Lambda bridge function
        task = progress.add_task("Creating Lambda API bridge...", total=None)
        lambda_role_arn = lambda_bridge.create_lambda_role(
            sess, lambda_role_name, account_id, config.region
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
        )
        state["resources"]["lambda_function_name"] = lambda_info["function_name"]
        state["resources"]["lambda_function_arn"] = lambda_info["function_arn"]
        state["resources"]["lambda_function_url"] = lambda_info["function_url"]
        save_state(config.project_dir, state)
        progress.update(task, description="[green]Lambda API bridge ready")
        progress.remove_task(task)

        # Step 7: CloudFront Distribution
        existing_dist = state["resources"].get("cloudfront_distribution_id")
        if existing_dist:
            task = progress.add_task("CloudFront distribution already exists", total=None)
            dist_info = cloudfront.get_distribution(sess, existing_dist)
            progress.update(task, description="[green]CloudFront distribution exists")
            progress.remove_task(task)
        else:
            task = progress.add_task("Creating CloudFront distribution...", total=None)
            oac_id = state["resources"].get("oac_id")
            if not oac_id:
                oac_id = cloudfront.create_origin_access_control(sess, f"{prefix}-oac")
                state["resources"]["oac_id"] = oac_id
                save_state(config.project_dir, state)

            dist_info = cloudfront.create_distribution(
                sess,
                bucket_name=bucket_name,
                region=config.region,
                oac_id=oac_id,
                lambda_function_url=lambda_info["function_url"],
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
