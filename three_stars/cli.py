"""CLI entry point for three-stars."""

from __future__ import annotations

import os
import sys
import warnings

import click
from rich.console import Console

from three_stars import __version__
from three_stars.config import ConfigError, load_config

warnings.filterwarnings("ignore", message=".*urllib3.*chardet.*")

console = Console()
err_console = Console(stderr=True)


@click.group()
@click.version_option(version=__version__, prog_name="three-stars")
def main() -> None:
    """Deploy AI-powered web applications to AWS with a single command.

    three-stars provisions a Bedrock AgentCore runtime, CloudFront distribution,
    and Lambda function to serve your AI app at a single URL.
    """


@main.command()
@click.argument("project_dir", default=".", type=click.Path())
@click.option("--region", default=None, help="AWS region (overrides config file).")
@click.option("--profile", default=None, help="AWS CLI profile name.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts.")
@click.option("--force", is_flag=True, help="Force re-creation of all resources.")
@click.option("--verbose", "-v", is_flag=True, help="Print detailed progress.")
def deploy(
    project_dir: str,
    region: str | None,
    profile: str | None,
    yes: bool,
    force: bool,
    verbose: bool,
) -> None:
    """Deploy the project to AWS.

    Reads three-stars.yml from PROJECT_DIR, provisions AWS resources,
    and prints the CloudFront URL.

    Use --force to re-create all resources from scratch.
    """
    try:
        config = load_config(project_dir, region_override=region)
    except ConfigError as e:
        err_console.print(f"[red]Configuration error:[/red] {e}")
        sys.exit(1)

    if not yes:
        msg = f"\n[bold]Deploying [cyan]{config.name}[/cyan]"
        msg += f" to [yellow]{config.region}[/yellow][/bold]\n"
        console.print(msg)
        if not click.confirm("Proceed with deployment?"):
            console.print("[dim]Cancelled.[/dim]")
            return

    from three_stars.deploy import run_deploy

    try:
        result = run_deploy(config, profile=profile, force=force, verbose=verbose)
        console.print()
        console.print("[bold green]Deployed successfully![/bold green]")
        console.print(f"[bold]URL:[/bold] https://{result['cloudfront_domain']}")
        console.print()
        console.print("[dim]Recovery commands:[/dim]")
        if os.path.isdir(os.path.join(project_dir, ".git")):
            console.print(
                "[dim]  Revert code: git checkout HEAD~1 -- agent/ app/ && sss deploy[/dim]"
            )
        console.print("[dim]  Clean slate: sss destroy --yes && sss deploy[/dim]")
    except Exception as e:
        err_console.print(f"[red]Deployment failed:[/red] {e}")
        err_console.print()
        err_console.print("[yellow]State has been saved. To recover:[/yellow]")
        err_console.print("  Check status:  sss status")
        err_console.print("  Retry deploy:  sss deploy")
        err_console.print("  Clean up:      sss destroy")
        sys.exit(1)


@main.command()
@click.argument("project_dir", default=".", type=click.Path())
@click.option("--region", default=None, help="AWS region (overrides config file).")
@click.option("--profile", default=None, help="AWS CLI profile name.")
@click.option("--sync", is_flag=True, help="Refresh state from AWS before showing status.")
def status(project_dir: str, region: str | None, profile: str | None, sync: bool) -> None:
    """Show deployment status.

    Reads the deployment state file and queries AWS for current resource status.
    Use --sync to discover actual resources from AWS and update the state file.
    """
    from three_stars.status import run_status

    config = None
    if sync or region:
        try:
            config = load_config(project_dir, region_override=region)
        except ConfigError:
            if sync:
                err_console.print("[red]--sync requires a valid three-stars.yml config file.[/red]")
                sys.exit(1)

    try:
        run_status(project_dir, profile=profile, sync=sync, config=config)
    except Exception as e:
        err_console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@main.command()
@click.argument("project_dir", default=".", type=click.Path())
@click.option("--region", default=None, help="AWS region (overrides config file).")
@click.option("--profile", default=None, help="AWS CLI profile name.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@click.option(
    "--name",
    default=None,
    help="Project name for tag-based discovery (when state file is missing).",
)
@click.option("--verbose", "-v", is_flag=True, help="Print detailed progress.")
def destroy(
    project_dir: str,
    region: str | None,
    profile: str | None,
    yes: bool,
    name: str | None,
    verbose: bool,
) -> None:
    """Destroy all deployed AWS resources.

    Reads the deployment state file and tears down resources in reverse order.
    Use --name to discover resources by tag when the state file is missing.
    """
    from three_stars.destroy import run_destroy

    try:
        run_destroy(
            project_dir,
            profile=profile,
            skip_confirm=yes,
            name=name,
            region=region,
            verbose=verbose,
        )
    except Exception as e:
        err_console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@main.command()
@click.argument("name", default="my-ai-app")
@click.option("--template", default="starter", help="Project template to use.")
def init(name: str, template: str) -> None:
    """Create a new three-stars project.

    Scaffolds a project directory with config, frontend, and agent templates.
    """
    from three_stars.init import run_init

    try:
        run_init(name, template=template)
    except Exception as e:
        err_console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
