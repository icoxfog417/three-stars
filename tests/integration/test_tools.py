"""Integration tests for the agent tools module.

These tests exercise the full MCP tool loading pipeline with real AWS
connections — no mocks. They verify:
  1. _resolve_command_path finds uvx
  2. get_tools() loads real MCPClients from mcp.json
  3. Agent(tools=clients) succeeds with real MCP servers

Run with:  uv run pytest tests/integration/test_tools.py -v -s
Requires:  AWS credentials and network access to MCP endpoints.
"""

import importlib
import json
import os
import sys
from pathlib import Path

import pytest
from strands import Agent
from strands.tools.mcp import MCPClient

_TEMPLATE_DIR = str(
    Path(__file__).resolve().parent.parent.parent / "three_stars_templates" / "starter" / "agent"
)
_MCP_JSON_PATH = Path(_TEMPLATE_DIR) / "mcp.json"

# Real MCP server configs used in production
_STDIO_SERVER = {
    "aws-mcp": {
        "command": "uvx",
        "args": [
            "mcp-proxy-for-aws@latest",
            "https://aws-mcp.us-east-1.api.aws/mcp",
            "--metadata",
            "AWS_REGION=us-east-1",
        ],
    }
}

_HTTP_SERVER = {
    "aws-knowledge-mcp-server": {
        "url": "https://knowledge-mcp.global.api.aws",
        "type": "http",
    }
}


@pytest.fixture()
def tools_module():
    """Import (or reimport) the starter agent tools module."""
    for mod_name in ("tools", "agent", "memory"):
        sys.modules.pop(mod_name, None)
    if _TEMPLATE_DIR not in sys.path:
        sys.path.insert(0, _TEMPLATE_DIR)
    try:
        mod = importlib.import_module("tools")
        yield mod
    finally:
        sys.path.remove(_TEMPLATE_DIR)
        for mod_name in ("tools", "agent", "memory"):
            sys.modules.pop(mod_name, None)


@pytest.fixture()
def mcp_json():
    """Write mcp.json next to tools.py in the template dir; remove after test."""
    written = False

    def _write(config: dict):
        nonlocal written
        _MCP_JSON_PATH.write_text(json.dumps(config))
        written = True

    yield _write

    if written:
        _MCP_JSON_PATH.unlink(missing_ok=True)


# ── _resolve_command_path ─────────────────────────────────────────────────


class TestResolveCommandPath:
    def test_uvx_resolves_to_absolute_path(self, tools_module):
        """uvx is installed — _resolve_command_path returns its absolute path."""
        result = tools_module._resolve_command_path("uvx")
        assert os.path.isabs(result), f"expected absolute path, got: {result}"
        assert "uvx" in result
        assert os.path.exists(result), f"resolved path does not exist: {result}"

    def test_nonexistent_command_returns_original(self, tools_module):
        result = tools_module._resolve_command_path("nonexistent-xyz-cmd")
        assert result == "nonexistent-xyz-cmd"


# ── Stdio MCP server: real AWS connection ────────────────────────────────


class TestStdioServer:
    """Load aws-mcp (stdio), create real Agent with MCP tools."""

    def test_load_and_create_agent(self, tools_module, mcp_json):
        """Full pipeline: mcp.json → get_tools → Agent(tools=clients) succeeds."""
        mcp_json({"mcpServers": _STDIO_SERVER})

        clients = tools_module.get_tools()
        assert len(clients) == 1
        assert isinstance(clients[0], MCPClient)

        agent = Agent(tools=clients)
        tool_names = list(agent.tool_registry.get_all_tools_config().keys())
        assert len(tool_names) > 0
        # All tools prefixed with mcp__{server_name}__
        assert all(name.startswith("mcp__aws-mcp__") for name in tool_names)
        for client in clients:
            client.stop(None, None, None)


# ── HTTP MCP server: real AWS connection ─────────────────────────────────


class TestHttpServer:
    """Load aws-knowledge-mcp-server (http), create real Agent with MCP tools."""

    def test_load_and_create_agent(self, tools_module, mcp_json):
        mcp_json({"mcpServers": _HTTP_SERVER})

        clients = tools_module.get_tools()
        assert len(clients) == 1
        assert isinstance(clients[0], MCPClient)

        agent = Agent(tools=clients)
        tool_names = list(agent.tool_registry.get_all_tools_config().keys())
        assert len(tool_names) > 0
        assert all(name.startswith("mcp__aws-knowledge-mcp-server__") for name in tool_names)
        for client in clients:
            client.stop(None, None, None)


# ── Mixed config: both server types ──────────────────────────────────────


class TestMixedConfig:
    def test_stdio_and_http_no_collision(self, tools_module, mcp_json):
        """aws-mcp and aws-knowledge share raw tool names but prefixes prevent collision."""
        mcp_json({"mcpServers": {**_STDIO_SERVER, **_HTTP_SERVER}})

        clients = tools_module.get_tools()
        assert len(clients) == 2
        assert all(isinstance(c, MCPClient) for c in clients)

        agent = Agent(tools=clients)
        tool_names = list(agent.tool_registry.get_all_tools_config().keys())
        assert len(tool_names) > 0

        # Both prefixes present — no collision
        aws_mcp_tools = [n for n in tool_names if n.startswith("mcp__aws-mcp__")]
        knowledge_tools = [n for n in tool_names if n.startswith("mcp__aws-knowledge-mcp-server__")]
        assert len(aws_mcp_tools) > 0
        assert len(knowledge_tools) > 0

        for client in clients:
            client.stop(None, None, None)
