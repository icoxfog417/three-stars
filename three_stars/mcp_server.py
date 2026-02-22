"""MCP server exposing three-stars CLI commands as tools."""

import asyncio
import os
import shutil

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("three-stars")


def _find_sss() -> str:
    """Locate the ``sss`` CLI entry point."""
    path = shutil.which("sss")
    if path:
        return path
    # Fallback: same directory as this Python interpreter
    import sys

    candidate = os.path.join(os.path.dirname(sys.executable), "sss")
    if os.path.isfile(candidate):
        return candidate
    return "sss"


async def _run(args: list[str]) -> str:
    """Run ``sss <args>`` and return combined stdout+stderr."""
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}
    proc = await asyncio.create_subprocess_exec(
        _find_sss(),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    output = (stdout.decode() + stderr.decode()).strip()
    if proc.returncode != 0:
        return f"Command failed (exit {proc.returncode}):\n{output}"
    return output


@mcp.tool()
async def sss_init(name: str = "my-ai-app", template: str = "starter") -> str:
    """Create a new three-stars project.

    Scaffolds a project directory with config, frontend, and agent templates.
    """
    return await _run(["init", name, "--template", template])


@mcp.tool()
async def sss_deploy(
    project_dir: str = ".",
    region: str | None = None,
    profile: str | None = None,
    force: bool = False,
    verbose: bool = False,
) -> str:
    """Deploy the project to AWS.

    Provisions S3, Bedrock AgentCore, Lambda@Edge, and CloudFront resources.
    """
    args = ["deploy", project_dir, "--yes"]
    if region:
        args += ["--region", region]
    if profile:
        args += ["--profile", profile]
    if force:
        args.append("--force")
    if verbose:
        args.append("--verbose")
    return await _run(args)


@mcp.tool()
async def sss_status(
    project_dir: str = ".",
    region: str | None = None,
    profile: str | None = None,
    sync: bool = False,
) -> str:
    """Show deployment status of AWS resources."""
    args = ["status", project_dir]
    if region:
        args += ["--region", region]
    if profile:
        args += ["--profile", profile]
    if sync:
        args.append("--sync")
    return await _run(args)


@mcp.tool()
async def sss_destroy(
    project_dir: str = ".",
    region: str | None = None,
    profile: str | None = None,
    name: str | None = None,
    verbose: bool = False,
) -> str:
    """Destroy all deployed AWS resources."""
    args = ["destroy", project_dir, "--yes"]
    if region:
        args += ["--region", region]
    if profile:
        args += ["--profile", profile]
    if name:
        args += ["--name", name]
    if verbose:
        args.append("--verbose")
    return await _run(args)


def main():
    """Run the MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
