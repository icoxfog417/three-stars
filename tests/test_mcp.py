"""Tests for the tools module in the starter agent template.

These tests exercise get_tools() and its internal helpers (_resolve_env_refs,
_make_stdio_client, _make_http_client) that the deployed agent uses to build
MCPClients from an mcp.json configuration file.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_TEMPLATE_DIR = str(Path(__file__).resolve().parent.parent / "templates" / "starter" / "agent")


@pytest.fixture(autouse=True)
def _patch_agent_deps():
    """Stub out heavy runtime deps that aren't installed in the test env."""
    # bedrock_agentcore.runtime is only available in the deployed agent container
    fake_runtime = MagicMock()
    fake_runtime.BedrockAgentCoreApp.return_value = MagicMock()
    sys.modules.setdefault("bedrock_agentcore", MagicMock())
    sys.modules.setdefault("bedrock_agentcore.runtime", fake_runtime)
    sys.modules.setdefault("bedrock_agentcore.memory", MagicMock())
    sys.modules.setdefault("bedrock_agentcore.memory.integrations", MagicMock())
    sys.modules.setdefault("bedrock_agentcore.memory.integrations.strands", MagicMock())
    sys.modules.setdefault("bedrock_agentcore.memory.integrations.strands.config", MagicMock())
    sys.modules.setdefault(
        "bedrock_agentcore.memory.integrations.strands.session_manager",
        MagicMock(),
    )


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


# ── _resolve_env_refs ─────────────────────────────────────────────────────


class TestResolveEnvRefs:
    def test_simple_substitution(self, tools_module, monkeypatch):
        monkeypatch.setenv("MY_KEY", "hello")
        assert tools_module._resolve_env_refs("${MY_KEY}") == "hello"

    def test_embedded_substitution(self, tools_module, monkeypatch):
        monkeypatch.setenv("TOKEN", "abc123")
        result = tools_module._resolve_env_refs("Bearer ${TOKEN} ok")
        assert result == "Bearer abc123 ok"

    def test_missing_var_resolves_to_empty(self, tools_module, monkeypatch):
        monkeypatch.delenv("NONEXISTENT", raising=False)
        assert tools_module._resolve_env_refs("${NONEXISTENT}") == ""

    def test_no_refs_unchanged(self, tools_module):
        assert tools_module._resolve_env_refs("plain-text") == "plain-text"

    def test_multiple_refs(self, tools_module, monkeypatch):
        monkeypatch.setenv("A", "1")
        monkeypatch.setenv("B", "2")
        assert tools_module._resolve_env_refs("${A}-${B}") == "1-2"


# ── _make_stdio_client (basic uvx case) ──────────────────────────────────


class TestMakeStdioClient:
    """Test case 1: basic uvx (command-based) MCP server."""

    def test_basic_uvx_server(self, tools_module):
        server = {
            "command": "uvx",
            "args": ["strands-agents-mcp-server"],
        }
        mock_MCPClient = MagicMock()
        tools_module._make_stdio_client(mock_MCPClient, server, {})

        # MCPClient was called once with a transport callable
        mock_MCPClient.assert_called_once()
        transport_callable = mock_MCPClient.call_args[0][0]
        assert callable(transport_callable)

    def test_aws_env_forwarded(self, tools_module):
        server = {"command": "uvx", "args": ["tool"]}
        aws_env = {
            "AWS_ACCESS_KEY_ID": "AKIA...",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "AWS_DEFAULT_REGION": "us-east-1",
        }

        mock_MCPClient = MagicMock()

        with (
            patch("mcp.client.stdio.stdio_client"),
            patch("mcp.client.stdio.StdioServerParameters") as mock_params,
        ):
            tools_module._make_stdio_client(mock_MCPClient, server, aws_env)
            # Invoke the transport callable to trigger StdioServerParameters
            transport_callable = mock_MCPClient.call_args[0][0]
            transport_callable()

            # StdioServerParameters should receive the forwarded AWS env
            params_kwargs = mock_params.call_args
            env_passed = params_kwargs.kwargs.get("env") or params_kwargs[1].get("env")
            assert "AWS_ACCESS_KEY_ID" in env_passed
            assert env_passed["AWS_DEFAULT_REGION"] == "us-east-1"

    def test_user_env_resolved(self, tools_module, monkeypatch):
        """Env vars with ${VAR} syntax are resolved from os.environ."""
        monkeypatch.setenv("SECRET_KEY", "s3cret")
        server = {
            "command": "uvx",
            "args": ["tool"],
            "env": {"API_KEY": "${SECRET_KEY}", "STATIC": "plain"},
        }

        mock_MCPClient = MagicMock()
        with (
            patch("mcp.client.stdio.stdio_client"),
            patch("mcp.client.stdio.StdioServerParameters") as mock_params,
        ):
            tools_module._make_stdio_client(mock_MCPClient, server, {})
            transport_callable = mock_MCPClient.call_args[0][0]
            transport_callable()

            kw = mock_params.call_args.kwargs
            env_passed = kw.get("env") or mock_params.call_args[1].get("env")
            assert env_passed["API_KEY"] == "s3cret"
            assert env_passed["STATIC"] == "plain"


# ── _make_http_client (remote MCP / URL-based) ──────────────────────────


class TestMakeHttpClient:
    """Test case 2: remote HTTP MCP server (url-based)."""

    def test_basic_http_server(self, tools_module):
        server = {
            "url": "https://knowledge-mcp.global.api.aws",
            "type": "http",
        }
        mock_MCPClient = MagicMock()
        tools_module._make_http_client(mock_MCPClient, server)

        mock_MCPClient.assert_called_once()
        transport_callable = mock_MCPClient.call_args[0][0]
        assert callable(transport_callable)

    def test_http_url_with_env_resolution(self, tools_module, monkeypatch):
        """Test case 3: URL containing ${VAR} (e.g. Tavily API key in URL)."""
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-test123")
        server = {
            "url": "https://mcp.tavily.com/mcp/?tavilyApiKey=${TAVILY_API_KEY}",
        }

        mock_MCPClient = MagicMock()
        with patch("mcp.client.streamable_http.streamablehttp_client") as mock_http:
            tools_module._make_http_client(mock_MCPClient, server)
            transport_callable = mock_MCPClient.call_args[0][0]
            transport_callable()

            mock_http.assert_called_once()
            call_kwargs = mock_http.call_args
            url_passed = call_kwargs.kwargs.get("url") or call_kwargs[1].get("url")
            assert url_passed == "https://mcp.tavily.com/mcp/?tavilyApiKey=tvly-test123"

    def test_http_with_headers(self, tools_module, monkeypatch):
        monkeypatch.setenv("AUTH_TOKEN", "bearer-xyz")
        server = {
            "url": "https://api.example.com/mcp",
            "headers": {"Authorization": "Bearer ${AUTH_TOKEN}"},
        }

        mock_MCPClient = MagicMock()
        with patch("mcp.client.streamable_http.streamablehttp_client") as mock_http:
            tools_module._make_http_client(mock_MCPClient, server)
            transport_callable = mock_MCPClient.call_args[0][0]
            transport_callable()

            call_kwargs = mock_http.call_args
            headers_passed = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
            assert headers_passed == {"Authorization": "Bearer bearer-xyz"}


# ── get_tools (integration: mixed config) ───────────────────────


class TestGetTools:
    def test_no_mcp_json_returns_empty(self, tools_module, tmp_path, monkeypatch):
        """When mcp.json doesn't exist, returns empty list."""
        monkeypatch.setattr(tools_module, "__file__", str(tmp_path / "tools.py"))
        result = tools_module.get_tools()
        assert result == []

    def test_mixed_config_stdio_and_http(self, tools_module, tmp_path, monkeypatch):
        """Config with both command-based and url-based servers."""
        mcp_config = {
            "mcpServers": {
                "aws-mcp": {
                    "command": "uvx",
                    "args": ["mcp-proxy-for-aws@latest", "https://aws-mcp.us-east-1.api.aws/mcp"],
                },
                "knowledge": {
                    "url": "https://knowledge-mcp.global.api.aws",
                    "type": "http",
                },
                "unknown": {
                    "type": "websocket",
                    "disabled": True,
                },
            }
        }
        mcp_path = tmp_path / "mcp.json"
        mcp_path.write_text(json.dumps(mcp_config))

        monkeypatch.setattr(tools_module, "__file__", str(tmp_path / "tools.py"))

        with patch("boto3.Session") as mock_session:
            mock_session.return_value.get_credentials.return_value = None
            clients = tools_module.get_tools()

        # 2 clients: aws-mcp (stdio) + knowledge (http). 'unknown' skipped.
        assert len(clients) == 2

    def test_empty_servers_returns_empty(self, tools_module, tmp_path, monkeypatch):
        mcp_path = tmp_path / "mcp.json"
        mcp_path.write_text(json.dumps({"mcpServers": {}}))
        monkeypatch.setattr(tools_module, "__file__", str(tmp_path / "tools.py"))

        result = tools_module.get_tools()
        assert result == []
