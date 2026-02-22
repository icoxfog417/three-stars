"""Deployment orchestration for three-stars.

The orchestrator is the explicit dependency manager — it passes typed outputs
from earlier resources as inputs to later ones. Resource modules never
reference each other.
"""

from __future__ import annotations

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from three_stars.config import (
    ProjectConfig,
    get_resource_tags,
    tags_to_aws,
)
from three_stars.naming import compute_names
from three_stars.resources import agentcore, cdn, edge, storage
from three_stars.resources._base import AWSContext
from three_stars.state import (
    DeploymentState,
    backup_state,
    create_initial_state,
    load_state,
    save_state,
)

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
    ctx = AWSContext.create(region=config.region, profile=profile)
    names = compute_names(config, ctx.account_id)
    tags = get_resource_tags(config)
    aws_tags = tags_to_aws(tags)

    # Backup existing state before modifying
    backup_state(config.project_dir)

    # Load existing state or create new
    state = load_state(config.project_dir) or create_initial_state(config.name, config.region)
    is_update = state.agentcore is not None

    if is_update and not force:
        console.print("[dim]Existing deployment detected — updating resources.[/dim]")

    if force:
        console.print("[dim]Force mode — all resources will be re-created.[/dim]")
        state.agentcore = None
        state.storage = None
        state.edge = None
        state.cdn = None

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        # Step 1: Storage (S3 bucket — needed early for agent code upload)
        task = progress.add_task(_step_label(1, "Creating S3 storage..."), total=None)
        state.storage = storage.deploy(ctx, config, names, tags=aws_tags)
        save_state(config.project_dir, state)
        progress.update(
            task, description=_step_label(1, "[green]S3 storage ready"), completed=1, total=1
        )

        # Step 2: AgentCore (IAM role + runtime + endpoint)
        task = progress.add_task(_step_label(2, "Creating AgentCore resources..."), total=None)
        state.agentcore = agentcore.deploy(
            ctx,
            config,
            names,
            bucket_name=state.storage.s3_bucket,
            tags=aws_tags,
            existing=state.agentcore if not force else None,
        )
        save_state(config.project_dir, state)
        label = "AgentCore updated" if is_update and not force else "AgentCore ready"
        progress.update(task, description=_step_label(2, f"[green]{label}"), completed=1, total=1)

        # Step 3: Lambda@Edge SigV4 signer (us-east-1)
        task = progress.add_task(
            _step_label(3, "Creating Lambda@Edge function (us-east-1)..."), total=None
        )
        state.edge = edge.deploy(
            ctx,
            names,
            runtime_arn=state.agentcore.runtime_arn,
            region=config.region,
            tags=aws_tags,
            tags_dict=tags,
            existing=state.edge if not force else None,
        )
        save_state(config.project_dir, state)
        progress.update(
            task,
            description=_step_label(3, "[green]Lambda@Edge function ready"),
            completed=1,
            total=1,
        )

        # Step 4: CloudFront distribution (needs bucket, agentcore region, edge ARN)
        task = progress.add_task(_step_label(4, "Creating CloudFront distribution..."), total=None)
        state.cdn = cdn.deploy(
            ctx,
            config,
            names,
            bucket_name=state.storage.s3_bucket,
            agentcore_region=config.region,
            edge_function_arn=state.edge.function_arn,
            tags=tags,
            existing=state.cdn if not force else None,
        )
        save_state(config.project_dir, state)

        def _on_cf_poll(elapsed: float) -> None:
            mins, secs = divmod(int(elapsed), 60)
            progress.update(
                task,
                description=_step_label(
                    4, f"Waiting for CloudFront propagation... [dim]({mins}:{secs:02d})[/dim]"
                ),
            )

        cf_status = cdn.wait_for_deployed(
            ctx,
            state.cdn.distribution_id,
            max_wait=600,
            poll_interval=15,
            on_poll=_on_cf_poll,
        )
        if cf_status == "Deployed":
            progress.update(
                task, description=_step_label(4, "[green]CloudFront distribution deployed")
            )
        else:
            progress.update(
                task,
                description=_step_label(4, f"[yellow]CloudFront distribution ({cf_status})"),
            )
        progress.update(task, completed=1, total=1)

        # Step 5: AgentCore resource policy (restrict invocation to edge role)
        task = progress.add_task(_step_label(5, "Setting AgentCore resource policy..."), total=None)
        agentcore.set_resource_policy(
            ctx,
            runtime_arn=state.agentcore.runtime_arn,
            edge_role_arn=state.edge.role_arn,
        )
        save_state(config.project_dir, state)
        progress.update(
            task,
            description=_step_label(5, "[green]AgentCore resource policy set"),
            completed=1,
            total=1,
        )

    # Print verbose resource details after progress display
    if verbose:
        _print_resource_details(state)

    # Invalidate CloudFront cache on updates so frontend changes are visible immediately
    if is_update and state.cdn:
        cdn.invalidate_cache(ctx, state.cdn.distribution_id)

    # Post-deployment health check
    _print_health_check(ctx, state)

    return {
        "cloudfront_domain": state.cdn.domain if state.cdn else "",
        "cloudfront_distribution_id": state.cdn.distribution_id if state.cdn else "",
        "agentcore_runtime_id": state.agentcore.runtime_id if state.agentcore else "",
    }


def _print_resource_details(state: DeploymentState) -> None:
    """Print verbose resource details as a compact summary."""
    details: list[tuple[str, str]] = []
    if state.storage:
        details.append(("Bucket", state.storage.s3_bucket))
    if state.agentcore:
        details.append(("Runtime", state.agentcore.runtime_id))
        details.append(("IAM Role", state.agentcore.iam_role_arn))
    if state.edge:
        details.append(("Function", state.edge.function_arn))
        details.append(("Edge Role", state.edge.role_arn))
    if state.cdn:
        details.append(("Distribution", state.cdn.distribution_id))
        details.append(("Domain", state.cdn.domain))
    if details:
        console.print()
        for label, value in details:
            console.print(f"  [dim]{label}: {value}[/dim]")


def _print_health_check(ctx: AWSContext, state: DeploymentState) -> None:
    """Run a quick health check on deployed resources and print results."""
    table = Table(title="Post-Deployment Health Check")
    table.add_column("Resource", style="bold")
    table.add_column("ID / Name")
    table.add_column("Status")

    checks: list[tuple[str, str, str]] = []

    if state.storage:
        try:
            ctx.client("s3").head_bucket(Bucket=state.storage.s3_bucket)
            checks.append(("S3 Bucket", state.storage.s3_bucket, "[green]Active[/green]"))
        except Exception:
            checks.append(("S3 Bucket", state.storage.s3_bucket, "[red]Not Found[/red]"))

    if state.agentcore:
        ac_rows = agentcore.get_status(ctx, state.agentcore)
        for row in ac_rows:
            checks.append((row.resource, row.id, row.status))

    if state.edge:
        edge_rows = edge.get_status(ctx, state.edge)
        for row in edge_rows:
            checks.append((row.resource, row.id, row.status))

    if state.cdn:
        dist_id = state.cdn.distribution_id
        try:
            from three_stars.resources.cdn import _get_distribution

            dist = _get_distribution(ctx, dist_id)
            cf_status = dist["status"]
            if cf_status == "Deployed":
                checks.append(("CloudFront", dist_id, "[green]Deployed[/green]"))
            else:
                checks.append(("CloudFront", dist_id, f"[yellow]{cf_status}[/yellow]"))
        except Exception:
            checks.append(("CloudFront", dist_id, "[red]Not Found[/red]"))

    for resource, name, status in checks:
        table.add_row(resource, name, status)

    console.print()
    console.print(table)

    if state.cdn:
        cf_check = next((s for r, _, s in checks if r == "CloudFront"), "")
        if "yellow" in cf_check:
            console.print(
                f"\n[yellow]CloudFront is still propagating.[/yellow] "
                f"Your site will be available at https://{state.cdn.domain} "
                f"in approximately 5-10 minutes."
            )
