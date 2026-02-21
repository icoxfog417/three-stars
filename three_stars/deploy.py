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
from three_stars.resources import _base, agentcore, api_bridge, cdn, edge, storage
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
    sess = _base.create_session(region=config.region, profile=profile)
    account_id = _base.get_account_id(sess)
    names = compute_names(config, account_id)
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
        state.api_bridge = None
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
        state.storage = storage.deploy(sess, config, names, tags=aws_tags)
        save_state(config.project_dir, state)
        progress.update(task, description=_step_label(1, "[green]S3 storage ready"))
        progress.remove_task(task)

        # Step 2: AgentCore (IAM role + runtime + endpoint)
        task = progress.add_task(_step_label(2, "Creating AgentCore resources..."), total=None)
        if verbose and state.agentcore:
            console.print(f"  Updating AgentCore runtime {state.agentcore.runtime_id}...")
        state.agentcore = agentcore.deploy(
            sess,
            config,
            names,
            bucket_name=state.storage.s3_bucket,
            tags=aws_tags,
            existing=state.agentcore if not force else None,
        )
        save_state(config.project_dir, state)
        label = "AgentCore updated" if is_update and not force else "AgentCore ready"
        progress.update(task, description=_step_label(2, f"[green]{label}"))
        progress.remove_task(task)

        # Step 3: Lambda API bridge (needs agentcore.runtime_arn)
        task = progress.add_task(_step_label(3, "Creating Lambda API bridge..."), total=None)
        state.api_bridge = api_bridge.deploy(
            sess,
            config,
            names,
            agent_runtime_arn=state.agentcore.runtime_arn,
            endpoint_name=state.agentcore.endpoint_name,
            tags=aws_tags,
            tags_dict=tags,
        )
        save_state(config.project_dir, state)
        progress.update(task, description=_step_label(3, "[green]Lambda API bridge ready"))
        progress.remove_task(task)

        # Step 4: Lambda@Edge function (us-east-1)
        task = progress.add_task(
            _step_label(4, "Creating Lambda@Edge function (us-east-1)..."), total=None
        )
        state.edge = edge.deploy(
            sess,
            names,
            tags=aws_tags,
            tags_dict=tags,
            existing=state.edge if not force else None,
        )
        save_state(config.project_dir, state)
        progress.update(task, description=_step_label(4, "[green]Lambda@Edge function ready"))
        progress.remove_task(task)

        # Step 5: CloudFront distribution (needs bucket, function URL, edge ARN)
        task = progress.add_task(_step_label(5, "Creating CloudFront distribution..."), total=None)
        state.cdn = cdn.deploy(
            sess,
            config,
            names,
            bucket_name=state.storage.s3_bucket,
            lambda_function_url=state.api_bridge.function_url,
            lambda_function_name=state.api_bridge.function_name,
            edge_function_arn=state.edge.function_arn,
            tags=tags,
            existing=state.cdn if not force else None,
        )
        save_state(config.project_dir, state)
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
        "cloudfront_domain": state.cdn.domain if state.cdn else "",
        "cloudfront_distribution_id": state.cdn.distribution_id if state.cdn else "",
        "agentcore_runtime_id": state.agentcore.runtime_id if state.agentcore else "",
    }


def _print_health_check(sess, state: DeploymentState) -> None:
    """Run a quick health check on deployed resources and print results."""
    table = Table(title="Post-Deployment Health Check")
    table.add_column("Resource", style="bold")
    table.add_column("ID / Name")
    table.add_column("Status")

    checks: list[tuple[str, str, str]] = []

    if state.storage:
        try:
            sess.client("s3").head_bucket(Bucket=state.storage.s3_bucket)
            checks.append(("S3 Bucket", state.storage.s3_bucket, "[green]Active[/green]"))
        except Exception:
            checks.append(("S3 Bucket", state.storage.s3_bucket, "[red]Not Found[/red]"))

    if state.api_bridge:
        fn = state.api_bridge.function_name
        try:
            resp = sess.client("lambda").get_function(FunctionName=fn)
            fn_state = resp["Configuration"]["State"]
            if fn_state == "Active":
                checks.append(("Lambda Bridge", fn, "[green]Active[/green]"))
            else:
                checks.append(("Lambda Bridge", fn, f"[yellow]{fn_state}[/yellow]"))
        except Exception:
            checks.append(("Lambda Bridge", fn, "[red]Not Found[/red]"))

    if state.cdn:
        dist_id = state.cdn.distribution_id
        try:
            from three_stars.resources.cdn import _get_distribution

            dist = _get_distribution(sess, dist_id)
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
