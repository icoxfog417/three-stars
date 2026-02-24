"""MCP server exposing three-stars CLI commands as tools.

Provides MCP tools for initializing, deploying, checking status, and
destroying three-stars AI web applications on AWS.
"""

import asyncio
from functools import partial
from io import StringIO
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field
from rich.console import Console

mcp = FastMCP("three-stars")


async def _run_sync(func, consoles=None):
    """Run a sync callable in the default executor, capturing console output.

    Args:
        func: Zero-argument callable to run.
        consoles: List of ``(module, attr_name)`` pairs whose console
            attributes will be temporarily replaced with capturing consoles.

    Returns:
        ``(return_value, captured_text)`` tuple.
    """
    captures = []
    originals = []

    for mod, attr in consoles or []:
        original = getattr(mod, attr)
        originals.append((mod, attr, original))
        capture = Console(file=StringIO(), force_terminal=False)
        setattr(mod, attr, capture)
        captures.append(capture)

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, func)
    finally:
        for mod, attr, original in originals:
            setattr(mod, attr, original)

    text_parts = []
    for cap in captures:
        cap.file.seek(0)
        text_parts.append(cap.file.read())
    captured = "\n".join(t for t in text_parts if t).strip()

    return result, captured


@mcp.tool()
async def sss_init(
    name: Annotated[
        str,
        Field(description="Project name, also used as the directory name."),
    ] = "my-ai-app",
    template: Annotated[
        str,
        Field(description="Template to scaffold from. Available: 'starter'."),
    ] = "starter",
) -> str:
    """Create a new three-stars project directory.

    Scaffolds a project with three-stars.yml config, an app/ frontend
    directory, and an agent/ directory with a starter Bedrock agent.
    The project is ready to deploy with sss_deploy after creation.
    """
    from three_stars import init as init_mod

    try:
        _, output = await _run_sync(
            partial(init_mod.run_init, name=name, template=template),
            consoles=[(init_mod, "console")],
        )
        return output or f"Project '{name}' created successfully."
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
async def sss_deploy(
    project_dir: Annotated[
        str,
        Field(description="Path to the three-stars project directory containing three-stars.yml."),
    ] = ".",
    region: Annotated[
        str | None,
        Field(description="AWS region override (e.g. 'us-west-2'). Defaults to config value."),
    ] = None,
    profile: Annotated[
        str | None,
        Field(description="AWS CLI profile name for credentials."),
    ] = None,
    force: Annotated[
        bool,
        Field(description="Force re-creation of all resources instead of updating."),
    ] = False,
    verbose: Annotated[
        bool,
        Field(description="Print detailed resource IDs and ARNs during deployment."),
    ] = False,
) -> str:
    """Deploy a three-stars project to AWS.

    Provisions all required AWS resources: S3 bucket for static assets,
    Bedrock AgentCore runtime and endpoint for the AI agent,
    Lambda@Edge function for SigV4 request signing, and a CloudFront
    distribution as the public entry point.

    Returns the CloudFront URL, distribution ID, and AgentCore runtime ID
    on success. Re-running on an existing deployment performs an update.
    """
    from three_stars import deploy as deploy_mod
    from three_stars.config import load_config

    try:
        config = load_config(project_dir, region_override=region)
        result, output = await _run_sync(
            partial(
                deploy_mod.run_deploy,
                config=config,
                profile=profile,
                force=force,
                verbose=verbose,
            ),
            consoles=[(deploy_mod, "console")],
        )
        # Return structured info alongside captured output
        lines = []
        if output:
            lines.append(output)
        if isinstance(result, dict):
            domain = result.get("cloudfront_domain", "")
            if domain:
                lines.append(f"\nURL: https://{domain}")
            dist_id = result.get("cloudfront_distribution_id", "")
            if dist_id:
                lines.append(f"Distribution ID: {dist_id}")
            runtime_id = result.get("agentcore_runtime_id", "")
            if runtime_id:
                lines.append(f"AgentCore Runtime ID: {runtime_id}")
        return "\n".join(lines) or "Deployment complete."
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
async def sss_status(
    project_dir: Annotated[
        str,
        Field(description="Path to the three-stars project directory containing three-stars.yml."),
    ] = ".",
    region: Annotated[
        str | None,
        Field(description="AWS region override (e.g. 'us-west-2'). Defaults to config value."),
    ] = None,
    profile: Annotated[
        str | None,
        Field(description="AWS CLI profile name for credentials."),
    ] = None,
    sync: Annotated[
        bool,
        Field(
            description="If true, query AWS for live resource state instead of reading "
            "the local state file. Slower but reflects actual cloud state."
        ),
    ] = False,
) -> str:
    """Show deployment status of AWS resources for a three-stars project.

    Displays the project name, region, deployment timestamp, and a table
    of all provisioned resources (S3, AgentCore, Lambda@Edge, CloudFront)
    with their current status. Also shows the public CloudFront URL.

    By default reads from the local state file. Use sync=true to discover
    actual state from AWS (useful if state file is missing or stale).
    """
    from three_stars import status as status_mod

    try:
        config = None
        if sync:
            from three_stars.config import load_config

            config = load_config(project_dir, region_override=region)

        _, output = await _run_sync(
            partial(
                status_mod.run_status,
                project_dir=project_dir,
                profile=profile,
                sync=sync,
                config=config,
            ),
            consoles=[(status_mod, "console")],
        )
        return output or "No deployment found."
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
async def sss_destroy(
    project_dir: Annotated[
        str,
        Field(description="Path to the three-stars project directory containing three-stars.yml."),
    ] = ".",
    region: Annotated[
        str | None,
        Field(description="AWS region for resource lookup when no state file exists."),
    ] = None,
    profile: Annotated[
        str | None,
        Field(description="AWS CLI profile name for credentials."),
    ] = None,
    name: Annotated[
        str | None,
        Field(
            description="Project name for resource lookup when no state file or config exists. "
            "Used with region to find resources by their computed names."
        ),
    ] = None,
    verbose: Annotated[
        bool,
        Field(description="Print detailed resource ARNs during teardown."),
    ] = False,
) -> str:
    """Destroy all AWS resources deployed by a three-stars project.

    Tears down resources in dependency order: removes Lambda@Edge
    associations from CloudFront, deletes Lambda@Edge function and role,
    deletes AgentCore runtime and IAM role, empties and deletes S3 bucket,
    and disables/deletes the CloudFront distribution.

    Confirmation is skipped automatically (non-interactive MCP context).
    If no state file exists, uses the project config or --name/--region
    to discover resources from AWS before destroying.
    """
    from three_stars import destroy as destroy_mod

    try:
        _, output = await _run_sync(
            partial(
                destroy_mod.run_destroy,
                project_dir=project_dir,
                profile=profile,
                skip_confirm=True,
                name=name,
                region=region,
                verbose=verbose,
            ),
            consoles=[
                (destroy_mod, "console"),
                (destroy_mod, "err_console"),
            ],
        )
        return output or "Destroy complete."
    except Exception as e:
        return f"Error: {e}"


def main():
    """Run the MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
