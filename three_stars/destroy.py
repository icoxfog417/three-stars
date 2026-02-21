"""Teardown orchestration for three-stars."""

from __future__ import annotations

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from three_stars.aws import agentcore, cloudfront, lambda_bridge, s3, session
from three_stars.state import delete_state, load_state

console = Console()
err_console = Console(stderr=True)


def run_destroy(
    project_dir: str,
    profile: str | None = None,
    skip_confirm: bool = False,
) -> None:
    """Tear down all deployed AWS resources.

    Args:
        project_dir: Path to the project directory.
        profile: AWS CLI profile name.
        skip_confirm: Skip confirmation prompt.
    """
    state = load_state(project_dir)
    if state is None:
        console.print("[yellow]No deployment found.[/yellow] Nothing to destroy.")
        return

    resources = state.get("resources", {})
    region = state.get("region", "us-east-1")
    project_name = state.get("project_name", "unknown")

    msg = f"\n[bold red]Destroying[/bold red] [cyan]{project_name}[/cyan]"
    msg += f" in [yellow]{region}[/yellow]"
    console.print(msg)
    console.print("\nResources to be deleted:")
    for key, value in resources.items():
        if not key.endswith("_name") or key in (
            "cloudfront_function_name",
            "lambda_function_name",
            "agentcore_endpoint_name",
        ):
            console.print(f"  - {key}: {value}")

    if not skip_confirm and not click.confirm("\nThis action is irreversible. Continue?"):
        console.print("[dim]Cancelled.[/dim]")
        return

    sess = session.create_session(region=region, profile=profile)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        # Reverse order of creation

        # 1. CloudFront Distribution
        dist_id = resources.get("cloudfront_distribution_id")
        if dist_id:
            task = progress.add_task(
                "Disabling CloudFront distribution (may take several minutes)...", total=None
            )
            try:
                cloudfront.delete_distribution(sess, dist_id)
                progress.update(task, description="[green]CloudFront distribution deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]CloudFront distribution: {e}")
            progress.remove_task(task)

        # 2. OACs (S3 + Lambda)
        for oac_key, oac_label in [("oac_id", "S3 OAC"), ("lambda_oac_id", "Lambda OAC")]:
            oac_id = resources.get(oac_key)
            if oac_id:
                task = progress.add_task(f"Deleting {oac_label}...", total=None)
                try:
                    cloudfront.delete_origin_access_control(sess, oac_id)
                    progress.update(task, description=f"[green]{oac_label} deleted")
                except Exception as e:
                    progress.update(task, description=f"[yellow]{oac_label}: {e}")
                progress.remove_task(task)

        # 2b. Lambda@Edge function + role (us-east-1)
        edge_func_name = resources.get("edge_function_name")
        if edge_func_name:
            task = progress.add_task("Deleting Lambda@Edge function...", total=None)
            try:
                lambda_bridge.delete_edge_function(sess, edge_func_name)
                progress.update(task, description="[green]Lambda@Edge function deleted")
            except Exception as e:
                msg = f"[yellow]Lambda@Edge function: {e} (replicas removing)"
                progress.update(task, description=msg)
            progress.remove_task(task)

        edge_role_name = resources.get("edge_role_name")
        if edge_role_name:
            task = progress.add_task("Deleting Lambda@Edge IAM role...", total=None)
            try:
                lambda_bridge.delete_edge_role(sess, edge_role_name)
                progress.update(task, description="[green]Lambda@Edge IAM role deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]Lambda@Edge IAM role: {e}")
            progress.remove_task(task)

        # 3. Lambda bridge function
        lambda_func_name = resources.get("lambda_function_name")
        if lambda_func_name:
            task = progress.add_task("Deleting Lambda bridge function...", total=None)
            try:
                lambda_bridge.delete_lambda_function(sess, lambda_func_name)
                progress.update(task, description="[green]Lambda function deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]Lambda function: {e}")
            progress.remove_task(task)

        # 4. Lambda IAM role
        lambda_role_name = resources.get("lambda_role_name")
        if lambda_role_name:
            task = progress.add_task("Deleting Lambda IAM role...", total=None)
            try:
                lambda_bridge.delete_lambda_role(sess, lambda_role_name)
                progress.update(task, description="[green]Lambda IAM role deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]Lambda IAM role: {e}")
            progress.remove_task(task)

        # 5. AgentCore endpoint
        runtime_id = resources.get("agentcore_runtime_id")
        endpoint_name = resources.get("agentcore_endpoint_name")
        if runtime_id and endpoint_name:
            task = progress.add_task("Deleting AgentCore endpoint...", total=None)
            try:
                agentcore.delete_agent_runtime_endpoint(sess, runtime_id, endpoint_name)
                progress.update(task, description="[green]AgentCore endpoint deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]AgentCore endpoint: {e}")
            progress.remove_task(task)

        # 6. AgentCore Runtime
        if runtime_id:
            task = progress.add_task("Deleting AgentCore runtime...", total=None)
            try:
                agentcore.delete_agent_runtime(sess, runtime_id)
                progress.update(task, description="[green]AgentCore runtime deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]AgentCore runtime: {e}")
            progress.remove_task(task)

        # 7. S3 Bucket
        bucket_name = resources.get("s3_bucket")
        if bucket_name:
            task = progress.add_task("Emptying and deleting S3 bucket...", total=None)
            try:
                s3.delete_bucket(sess, bucket_name)
                progress.update(task, description="[green]S3 bucket deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]S3 bucket: {e}")
            progress.remove_task(task)

        # 8. AgentCore IAM Role
        role_name = resources.get("iam_role_name")
        if role_name:
            task = progress.add_task("Deleting AgentCore IAM role...", total=None)
            try:
                agentcore.delete_iam_role(sess, role_name)
                progress.update(task, description="[green]AgentCore IAM role deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]AgentCore IAM role: {e}")
            progress.remove_task(task)

    # Remove state file
    delete_state(project_dir)
    console.print("\n[bold green]All resources destroyed.[/bold green]")
