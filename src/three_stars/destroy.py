"""Teardown orchestration for three-stars."""

from __future__ import annotations

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from three_stars.aws import agentcore, cf_function, cloudfront, s3, session
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
        if not key.endswith("_name") or key == "cloudfront_function_name":
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

        # 2. OAC
        oac_id = resources.get("oac_id")
        if oac_id:
            task = progress.add_task("Deleting Origin Access Control...", total=None)
            try:
                cloudfront.delete_origin_access_control(sess, oac_id)
                progress.update(task, description="[green]OAC deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]OAC: {e}")
            progress.remove_task(task)

        # 3. CloudFront Function
        cf_func_name = resources.get("cloudfront_function_name")
        if cf_func_name:
            task = progress.add_task("Deleting CloudFront Function...", total=None)
            try:
                cf_function.delete_function(sess, cf_func_name)
                progress.update(task, description="[green]CloudFront Function deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]CloudFront Function: {e}")
            progress.remove_task(task)

        # 4. AgentCore Runtime
        runtime_id = resources.get("agentcore_runtime_id")
        if runtime_id:
            task = progress.add_task("Deleting AgentCore runtime...", total=None)
            try:
                agentcore.delete_agent_runtime(sess, runtime_id)
                progress.update(task, description="[green]AgentCore runtime deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]AgentCore runtime: {e}")
            progress.remove_task(task)

        # 5. S3 Bucket
        bucket_name = resources.get("s3_bucket")
        if bucket_name:
            task = progress.add_task("Emptying and deleting S3 bucket...", total=None)
            try:
                s3.delete_bucket(sess, bucket_name)
                progress.update(task, description="[green]S3 bucket deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]S3 bucket: {e}")
            progress.remove_task(task)

        # 6. IAM Role
        role_name = resources.get("iam_role_name")
        if role_name:
            task = progress.add_task("Deleting IAM role...", total=None)
            try:
                agentcore.delete_iam_role(sess, role_name)
                progress.update(task, description="[green]IAM role deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]IAM role: {e}")
            progress.remove_task(task)

    # Remove state file
    delete_state(project_dir)
    console.print("\n[bold green]All resources destroyed.[/bold green]")
