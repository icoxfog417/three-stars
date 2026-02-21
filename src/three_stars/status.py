"""Deployment status reporting for three-stars."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from three_stars.aws import agentcore, cloudfront, session
from three_stars.state import load_state

console = Console()


def run_status(
    project_dir: str,
    profile: str | None = None,
) -> None:
    """Display deployment status for a three-stars project.

    Args:
        project_dir: Path to the project directory.
        profile: AWS CLI profile name.
    """
    state = load_state(project_dir)
    if state is None:
        console.print("[yellow]No deployment found.[/yellow]")
        console.print("Run 'three-stars deploy' to deploy your project.")
        return

    resources = state.get("resources", {})
    region = state.get("region", "us-east-1")
    project_name = state.get("project_name", "unknown")

    console.print(f"\n[bold]Project:[/bold] [cyan]{project_name}[/cyan]")
    console.print(f"[bold]Region:[/bold] [yellow]{region}[/yellow]")
    console.print(f"[bold]Deployed at:[/bold] {state.get('deployed_at', 'unknown')}")

    # Create status table
    table = Table(title="Resource Status")
    table.add_column("Resource", style="bold")
    table.add_column("ID / Name")
    table.add_column("Status")

    sess = session.create_session(region=region, profile=profile)

    # S3 Bucket
    bucket_name = resources.get("s3_bucket", "")
    if bucket_name:
        bucket_status = _check_s3_bucket(sess, bucket_name)
        table.add_row("S3 Bucket", bucket_name, bucket_status)

    # AgentCore Runtime
    runtime_id = resources.get("agentcore_runtime_id", "")
    if runtime_id:
        runtime_status = _check_agentcore(sess, runtime_id)
        table.add_row("AgentCore Runtime", runtime_id, runtime_status)

    # CloudFront Distribution
    dist_id = resources.get("cloudfront_distribution_id", "")
    if dist_id:
        dist_status = _check_cloudfront(sess, dist_id)
        table.add_row("CloudFront Distribution", dist_id, dist_status)

    # CloudFront Function
    cf_func_name = resources.get("cloudfront_function_name", "")
    if cf_func_name:
        table.add_row("CloudFront Function", cf_func_name, "[green]Deployed[/green]")

    # IAM Role
    role_arn = resources.get("iam_role_arn", "")
    if role_arn:
        table.add_row("IAM Role", role_arn.split("/")[-1], "[green]Active[/green]")

    console.print()
    console.print(table)

    # Print URL
    domain = resources.get("cloudfront_domain", "")
    if domain:
        console.print(f"\n[bold]URL:[/bold] https://{domain}")


def _check_s3_bucket(sess, bucket_name: str) -> str:
    """Check S3 bucket status."""
    try:
        s3 = sess.client("s3")
        s3.head_bucket(Bucket=bucket_name)
        return "[green]Active[/green]"
    except Exception:
        return "[red]Not Found[/red]"


def _check_agentcore(sess, runtime_id: str) -> str:
    """Check AgentCore runtime status."""
    try:
        result = agentcore.get_agent_runtime_status(sess, runtime_id)
        status = result["status"]
        if status == "ACTIVE":
            return "[green]Active[/green]"
        elif status in ("CREATING", "UPDATING"):
            return f"[yellow]{status}[/yellow]"
        else:
            return f"[red]{status}[/red]"
    except Exception:
        return "[red]Not Found[/red]"


def _check_cloudfront(sess, distribution_id: str) -> str:
    """Check CloudFront distribution status."""
    try:
        result = cloudfront.get_distribution(sess, distribution_id)
        status = result["status"]
        if status == "Deployed":
            return "[green]Deployed[/green]"
        else:
            return f"[yellow]{status}[/yellow]"
    except Exception:
        return "[red]Not Found[/red]"
