"""Teardown orchestration for three-stars."""

from __future__ import annotations

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from three_stars.resources import _base, agentcore, api_bridge, cdn, edge, storage
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

    region = state.region
    project_name = state.project_name

    msg = f"\n[bold red]Destroying[/bold red] [cyan]{project_name}[/cyan]"
    msg += f" in [yellow]{region}[/yellow]"
    console.print(msg)
    console.print("\nResources to be deleted:")
    if state.cdn:
        console.print(f"  - CloudFront: {state.cdn.distribution_id}")
    if state.edge:
        console.print(f"  - Lambda@Edge: {state.edge.function_name}")
    if state.api_bridge:
        console.print(f"  - Lambda Bridge: {state.api_bridge.function_name}")
    if state.agentcore:
        console.print(f"  - AgentCore Runtime: {state.agentcore.runtime_id}")
    if state.storage:
        console.print(f"  - S3 Bucket: {state.storage.s3_bucket}")

    if not skip_confirm and not click.confirm("\nThis action is irreversible. Continue?"):
        console.print("[dim]Cancelled.[/dim]")
        return

    sess = _base.create_session(region=region, profile=profile)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        # Reverse order of creation

        # 1. CloudFront Distribution + OACs
        if state.cdn:
            task = progress.add_task(
                "Disabling CloudFront distribution (may take several minutes)...",
                total=None,
            )
            try:
                cdn.destroy(sess, state.cdn)
                progress.update(task, description="[green]CloudFront + OACs deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]CloudFront: {e}")
            progress.remove_task(task)

        # 2. Lambda@Edge function + role (us-east-1)
        if state.edge:
            task = progress.add_task("Deleting Lambda@Edge function...", total=None)
            try:
                edge.destroy(sess, state.edge)
                progress.update(task, description="[green]Lambda@Edge deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]Lambda@Edge: {e} (replicas removing)")
            progress.remove_task(task)

        # 3. Lambda bridge function + role
        if state.api_bridge:
            task = progress.add_task("Deleting Lambda bridge...", total=None)
            try:
                api_bridge.destroy(sess, state.api_bridge)
                progress.update(task, description="[green]Lambda bridge deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]Lambda bridge: {e}")
            progress.remove_task(task)

        # 4. AgentCore endpoint + runtime + IAM role
        if state.agentcore:
            task = progress.add_task("Deleting AgentCore resources...", total=None)
            try:
                agentcore.destroy(sess, state.agentcore)
                progress.update(task, description="[green]AgentCore resources deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]AgentCore: {e}")
            progress.remove_task(task)

        # 5. S3 Bucket
        if state.storage:
            task = progress.add_task("Emptying and deleting S3 bucket...", total=None)
            try:
                storage.destroy(sess, state.storage)
                progress.update(task, description="[green]S3 bucket deleted")
            except Exception as e:
                progress.update(task, description=f"[yellow]S3 bucket: {e}")
            progress.remove_task(task)

    # Remove state file
    delete_state(project_dir)
    console.print("\n[bold green]All resources destroyed.[/bold green]")
