"""Teardown orchestration for three-stars."""

from __future__ import annotations

import contextlib

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from three_stars.config import ConfigError, ProjectConfig, load_config
from three_stars.resources import agentcore, cdn, edge, storage
from three_stars.resources._base import AWSContext
from three_stars.state import delete_state, load_state, save_state
from three_stars.status import refresh_state

console = Console()
err_console = Console(stderr=True)

TOTAL_STEPS = 5


def _step_label(step: int, description: str) -> str:
    return f"[{step}/{TOTAL_STEPS}] {description}"


def run_destroy(
    project_dir: str,
    profile: str | None = None,
    skip_confirm: bool = False,
    name: str | None = None,
    region: str | None = None,
    verbose: bool = False,
) -> None:
    """Tear down all deployed AWS resources.

    Args:
        project_dir: Path to the project directory.
        profile: AWS CLI profile name.
        skip_confirm: Skip confirmation prompt.
        name: Project name for name-based lookup (fallback when no state file).
        region: AWS region for name-based lookup.
        verbose: Print detailed progress information.
    """
    state = load_state(project_dir)

    if state is None:
        # Resolve project config: explicit --name flag, or read from config file
        config: ProjectConfig | None = None
        if name:
            config = ProjectConfig(name=name, region=region or "us-east-1")
        else:
            with contextlib.suppress(ConfigError):
                config = load_config(project_dir)

        if config:
            ctx = AWSContext.create(region=config.region, profile=profile)
            console.print(
                f"[dim]No state file found. Looking up resources "
                f"for project '{config.name}'...[/dim]"
            )
            state = refresh_state(ctx, config, project_dir)

        if state is None:
            console.print(
                "[yellow]No deployment found.[/yellow] Nothing to destroy.\n"
                "[dim]Tip: use --name to look up resources by project name.[/dim]"
            )
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
    if state.agentcore:
        console.print(f"  - AgentCore Runtime: {state.agentcore.runtime_id}")
        if state.agentcore.memory_id:
            console.print(f"  - AgentCore Memory: {state.agentcore.memory_id}")
    if state.storage:
        console.print(f"  - S3 Bucket: {state.storage.s3_bucket}")

    if not skip_confirm and not click.confirm("\nThis action is irreversible. Continue?"):
        console.print("[dim]Cancelled.[/dim]")
        return

    ctx = AWSContext.create(region=region, profile=profile)

    errors: list[str] = []
    results: list[tuple[str, str, str]] = []  # (resource, id/name, status)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        step = 1

        # ── Phase 1: Remove Lambda@Edge associations from CloudFront ──
        if state.cdn and state.edge:
            dist_id = state.cdn.distribution_id
            task = progress.add_task(
                _step_label(step, f"Removing Lambda@Edge association from {dist_id}..."),
                total=None,
            )
            try:
                cdn.remove_edge_associations(ctx, dist_id)
                progress.update(
                    task,
                    description=_step_label(step, "[green]Lambda@Edge association removed"),
                )
                results.append(("CloudFront Update", dist_id, "[green]Disassociated[/green]"))
            except Exception as e:
                errors.append(f"CloudFront update: {e}")
                progress.update(task, description=_step_label(step, f"[yellow]CloudFront: {e}"))
                results.append(("CloudFront Update", dist_id, f"[red]{e}[/red]"))
            progress.update(task, completed=1, total=1)
            step += 1

        # ── Phase 2: Delete Lambda@Edge function + role ──
        edge_fn_name: str | None = None
        if state.edge:
            fn_name = state.edge.function_name
            edge_fn_name = fn_name
            task = progress.add_task(
                _step_label(step, f"Deleting Lambda@Edge function {fn_name}..."),
                total=None,
            )
            if verbose:
                console.print(f"  ARN: {state.edge.function_arn}")
                console.print(f"  Role: {state.edge.role_arn}")
            try:
                deleted = edge.destroy(ctx, state.edge)
                if deleted:
                    results.append(("Lambda@Edge", fn_name, "[green]Deleted[/green]"))
                    state.edge = None
                    progress.update(
                        task,
                        description=_step_label(step, "[green]Lambda@Edge deleted"),
                    )
                else:
                    results.append(
                        (
                            "Lambda@Edge",
                            fn_name,
                            "[yellow]Pending replica cleanup[/yellow]",
                        )
                    )
                    state.edge = None
                    progress.update(
                        task,
                        description=_step_label(
                            step,
                            "[green]Lambda@Edge disassociated[/green] "
                            "[dim](function will become deletable after replica cleanup)[/dim]",
                        ),
                    )
            except Exception as e:
                errors.append(f"Lambda@Edge: {e}")
                progress.update(task, description=_step_label(step, f"[yellow]Lambda@Edge: {e}"))
                results.append(("Lambda@Edge", fn_name, f"[red]{e}[/red]"))
            progress.update(task, completed=1, total=1)
            step += 1

        # ── Phase 3: AgentCore + S3 + CloudFront (no dependency) ──

        if state.agentcore:
            runtime_id = state.agentcore.runtime_id
            task = progress.add_task(
                _step_label(step, f"Deleting AgentCore runtime {runtime_id}..."),
                total=None,
            )
            if verbose:
                console.print(f"  Runtime ARN: {state.agentcore.runtime_arn}")
            try:
                agentcore.destroy(ctx, state.agentcore)
                results.append(("AgentCore", runtime_id, "[green]Deleted[/green]"))
                state.agentcore = None
                progress.update(
                    task,
                    description=_step_label(step, "[green]AgentCore resources deleted"),
                )
            except Exception as e:
                errors.append(f"AgentCore: {e}")
                progress.update(task, description=_step_label(step, f"[yellow]AgentCore: {e}"))
                results.append(("AgentCore", runtime_id, f"[red]{e}[/red]"))
            progress.update(task, completed=1, total=1)
            step += 1

        if state.storage:
            bucket = state.storage.s3_bucket
            task = progress.add_task(
                _step_label(step, f"Emptying and deleting S3 bucket {bucket}..."),
                total=None,
            )
            try:
                storage.destroy(ctx, state.storage)
                results.append(("S3 Bucket", bucket, "[green]Deleted[/green]"))
                state.storage = None
                progress.update(task, description=_step_label(step, "[green]S3 bucket deleted"))
            except Exception as e:
                errors.append(f"S3 bucket: {e}")
                progress.update(task, description=_step_label(step, f"[yellow]S3 bucket: {e}"))
                results.append(("S3 Bucket", bucket, f"[red]{e}[/red]"))
            progress.update(task, completed=1, total=1)
            step += 1

        if state.cdn:
            dist_id = state.cdn.distribution_id
            task = progress.add_task(
                _step_label(step, f"Disabling CloudFront distribution {dist_id}..."),
                total=None,
            )
            if verbose:
                console.print(f"  Domain: {state.cdn.domain}")
            try:
                cdn.disable_and_delete_distribution(ctx, state.cdn)
                results.append(("CloudFront", dist_id, "[green]Deleted[/green]"))
                state.cdn = None
                progress.update(
                    task, description=_step_label(step, "[green]CloudFront + OACs deleted")
                )
            except Exception as e:
                errors.append(f"CloudFront: {e}")
                progress.update(task, description=_step_label(step, f"[yellow]CloudFront: {e}"))
                results.append(("CloudFront", dist_id, f"[red]{e}[/red]"))
            progress.update(task, completed=1, total=1)

    # ── Summary table ──
    _print_destroy_summary(results)

    # ── Lambda@Edge cleanup instructions ──
    if edge_fn_name and any("Pending replica cleanup" in status for _, _, status in results):
        console.print(
            "\n[yellow]Lambda@Edge replicas are still cleaning up.[/yellow]"
            "\nThe function will become deletable in ~15-60 minutes."
            "\nRun this command to finish cleanup later:\n"
        )
        console.print(
            f"  aws lambda delete-function --function-name {edge_fn_name} --region us-east-1"
        )

    if errors:
        save_state(project_dir, state)
        console.print(
            f"\n[bold yellow]{len(errors)} resource(s) failed to delete.[/bold yellow]"
            " State file preserved with remaining resources."
            " Run [cyan]destroy[/cyan] again to retry."
        )
    else:
        delete_state(project_dir)
        console.print("\n[bold green]All resources destroyed.[/bold green]")


def _print_destroy_summary(results: list[tuple[str, str, str]]) -> None:
    """Print a summary table of destroy results."""
    if not results:
        return
    table = Table(title="Destroy Summary")
    table.add_column("Resource", style="bold")
    table.add_column("ID / Name")
    table.add_column("Status")
    for resource, name, status in results:
        table.add_row(resource, name, status)
    console.print()
    console.print(table)
