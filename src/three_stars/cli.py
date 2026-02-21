"""CLI entry point for three-stars."""

from __future__ import annotations

import sys

import click
from rich.console import Console

from three_stars import __version__
from three_stars.config import ConfigError, load_config

console = Console()
err_console = Console(stderr=True)


@click.group()
@click.version_option(version=__version__, prog_name="sss")
def main() -> None:
    """Deploy AI-powered web applications to AWS with a single command.

    sss provisions a Bedrock AgentCore runtime, CloudFront distribution,
    and Lambda function to serve your AI app at a single URL.
    """


@main.command()
@click.argument("project_dir", default=".", type=click.Path())
@click.option("--region", default=None, help="AWS region (overrides config file).")
@click.option("--profile", default=None, help="AWS CLI profile name.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts.")
def deploy(project_dir: str, region: str | None, profile: str | None, yes: bool) -> None:
    """Deploy the project to AWS.

    Reads three-stars.yml from PROJECT_DIR, provisions AWS resources,
    and prints the CloudFront URL.
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
        result = run_deploy(config, profile=profile)
        console.print()
        console.print("[bold green]Deployed successfully![/bold green]")
        console.print(f"[bold]URL:[/bold] https://{result['cloudfront_domain']}")
    except Exception as e:
        err_console.print(f"[red]Deployment failed:[/red] {e}")
        sys.exit(1)


@main.command()
@click.argument("project_dir", default=".", type=click.Path())
@click.option("--region", default=None, help="AWS region (overrides config file).")
@click.option("--profile", default=None, help="AWS CLI profile name.")
def status(project_dir: str, region: str | None, profile: str | None) -> None:
    """Show deployment status.

    Reads the deployment state file and queries AWS for current resource status.
    """
    from three_stars.status import run_status

    try:
        run_status(project_dir, profile=profile)
    except Exception as e:
        err_console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@main.command()
@click.argument("project_dir", default=".", type=click.Path())
@click.option("--region", default=None, help="AWS region (overrides config file).")
@click.option("--profile", default=None, help="AWS CLI profile name.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
def destroy(project_dir: str, region: str | None, profile: str | None, yes: bool) -> None:
    """Destroy all deployed AWS resources.

    Reads the deployment state file and tears down resources in reverse order.
    """
    from three_stars.destroy import run_destroy

    try:
        run_destroy(project_dir, profile=profile, skip_confirm=yes)
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
