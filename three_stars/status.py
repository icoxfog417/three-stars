"""Deployment status reporting for three-stars."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from three_stars.resources import _base, agentcore, api_bridge, cdn, edge, storage
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
        console.print("Run 'sss deploy' to deploy your project.")
        return

    console.print(f"\n[bold]Project:[/bold] [cyan]{state.project_name}[/cyan]")
    console.print(f"[bold]Region:[/bold] [yellow]{state.region}[/yellow]")
    console.print(f"[bold]Deployed at:[/bold] {state.deployed_at}")

    # Create status table
    table = Table(title="Resource Status")
    table.add_column("Resource", style="bold")
    table.add_column("ID / Name")
    table.add_column("Status")

    sess = _base.create_session(region=state.region, profile=profile)

    # Collect status rows from each resource module
    if state.storage:
        for row in storage.get_status(sess, state.storage):
            table.add_row(row.resource, row.id, row.status)

    if state.agentcore:
        for row in agentcore.get_status(sess, state.agentcore):
            table.add_row(row.resource, row.id, row.status)

    if state.api_bridge:
        for row in api_bridge.get_status(sess, state.api_bridge):
            table.add_row(row.resource, row.id, row.status)

    if state.edge:
        for row in edge.get_status(sess, state.edge):
            table.add_row(row.resource, row.id, row.status)

    if state.cdn:
        for row in cdn.get_status(sess, state.cdn):
            table.add_row(row.resource, row.id, row.status)

    console.print()
    console.print(table)

    # Print URL
    if state.cdn:
        console.print(f"\n[bold]URL:[/bold] https://{state.cdn.domain}")
