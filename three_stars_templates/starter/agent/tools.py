"""MCP tool loading for three-stars agents.

Reads ``mcp.json`` next to this file and creates MCPClient instances for
each server entry.  Two transport types are supported:

- **stdio** (has ``command``): spawns a subprocess via ``stdio_client``
- **http** (has ``url``): connects via ``streamablehttp_client``

Environment variable references (``${VAR}``) in config values are resolved
from ``os.environ``.  AWS credentials from the current boto3 session are
forwarded to stdio subprocesses automatically.
"""

import json
import os
import re
import shutil
from pathlib import Path


def _resolve_env_refs(value: str) -> str:
    """Replace ${VAR} references in a string with os.environ values."""
    return re.sub(
        r"\$\{([^}]+)\}",
        lambda m: os.environ.get(m.group(1), ""),
        value,
    )


def get_tools() -> list:
    """Return MCP tool clients loaded from agent/mcp.json.

    Trigger: ``mcp.json`` exists next to this file.
    Returns an empty list when the file is absent or has no servers.
    """
    mcp_path = Path(__file__).parent / "mcp.json"
    if not mcp_path.exists():
        return []

    with open(mcp_path) as f:
        mcp_config = json.load(f)

    servers = mcp_config.get("mcpServers", {})
    if not servers:
        return []

    # Build AWS credential env from the current boto3 session (for stdio)
    import boto3
    from strands.tools.mcp import MCPClient

    session = boto3.Session()
    creds = session.get_credentials()
    aws_env: dict[str, str] = {}
    if creds:
        frozen = creds.get_frozen_credentials()
        aws_env["AWS_ACCESS_KEY_ID"] = frozen.access_key
        aws_env["AWS_SECRET_ACCESS_KEY"] = frozen.secret_key
        if frozen.token:
            aws_env["AWS_SESSION_TOKEN"] = frozen.token
    if os.environ.get("AWS_DEFAULT_REGION"):
        aws_env["AWS_DEFAULT_REGION"] = os.environ["AWS_DEFAULT_REGION"]

    clients: list[MCPClient] = []
    for name, server in servers.items():
        prefix = f"mcp__{name}__"
        if "command" in server:
            client = _make_stdio_client(MCPClient, server, aws_env, prefix=prefix)
        elif "url" in server:
            client = _make_http_client(MCPClient, server, prefix=prefix)
        else:
            continue
        clients.append(client)

    return clients


def _resolve_command_path(command: str) -> str:
    """Resolve a command to its full path.

    In the AgentCore runtime ``/var/task/bin/`` is not on PATH, so
    ``shutil.which`` may fail for pip-installed binaries like ``uvx``.
    Fall back to the ``uv`` package's own path discovery when available.
    """
    found = shutil.which(command)
    if found:
        return found

    # uvx ships alongside uv — use uv's path discovery as fallback
    if command == "uvx":
        try:
            from uv._find_uv import find_uv_bin

            uv_bin = find_uv_bin()
            return os.path.join(os.path.dirname(uv_bin), "uvx")
        except Exception:
            pass

    return command


def _make_stdio_client(MCPClient, server: dict, aws_env: dict[str, str], *, prefix: str):
    """Create an MCPClient for a stdio (command-based) MCP server."""
    from mcp.client.stdio import StdioServerParameters, stdio_client

    command = _resolve_command_path(_resolve_env_refs(server["command"]))
    args = [_resolve_env_refs(a) for a in server.get("args", [])]

    env = {**aws_env}
    for k, v in server.get("env", {}).items():
        env[k] = _resolve_env_refs(v)

    return MCPClient(
        lambda: stdio_client(StdioServerParameters(command=command, args=args, env=env)),
        prefix=prefix,
    )


def _make_http_client(MCPClient, server: dict, *, prefix: str):
    """Create an MCPClient for an HTTP (url-based) MCP server."""
    from mcp.client.streamable_http import streamablehttp_client

    url = _resolve_env_refs(server["url"])
    headers: dict[str, str] = {}
    for k, v in server.get("headers", {}).items():
        headers[k] = _resolve_env_refs(v)

    return MCPClient(
        lambda: streamablehttp_client(url=url, headers=headers or None),
        prefix=prefix,
    )
