"""Integration tests for the starter agent template.

These tests exercise the real template code (agent.py, tools.py, memory.py)
with no mocks.  All imports use the actual strands and bedrock_agentcore SDKs.

Tests that would require AWS credentials (model invocation, memory session
creation) are excluded — those belong in a live E2E test suite.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest

_TEMPLATE_DIR = str(
    Path(__file__).resolve().parent.parent.parent / "three_stars_templates" / "starter" / "agent"
)


@pytest.fixture(autouse=True)
def _template_path():
    """Put the template directory on sys.path for imports, clean up after."""
    sys.path.insert(0, _TEMPLATE_DIR)
    yield
    if _TEMPLATE_DIR in sys.path:
        sys.path.remove(_TEMPLATE_DIR)
    for mod_name in ("agent", "tools", "memory"):
        sys.modules.pop(mod_name, None)


def _import(name: str):
    sys.modules.pop(name, None)
    return importlib.import_module(name)


# ── tools.py ─────────────────────────────────────────────────────────────


class TestResolveEnvRefs:
    """_resolve_env_refs substitutes ${VAR} from os.environ."""

    def test_substitutes_env_var(self, monkeypatch):
        monkeypatch.setenv("TEST_SSS_VAR", "hello")
        tools = _import("tools")
        assert tools._resolve_env_refs("prefix-${TEST_SSS_VAR}-suffix") == "prefix-hello-suffix"

    def test_missing_var_becomes_empty(self):
        tools = _import("tools")
        assert tools._resolve_env_refs("${DEFINITELY_MISSING_SSS_VAR}") == ""

    def test_no_refs_unchanged(self):
        tools = _import("tools")
        assert tools._resolve_env_refs("plain-string") == "plain-string"

    def test_multiple_refs(self, monkeypatch):
        monkeypatch.setenv("A_SSS", "1")
        monkeypatch.setenv("B_SSS", "2")
        tools = _import("tools")
        assert tools._resolve_env_refs("${A_SSS}-${B_SSS}") == "1-2"


class TestResolveCommandPath:
    """_resolve_command_path finds real executables or falls through."""

    def test_finds_python(self):
        tools = _import("tools")
        result = tools._resolve_command_path("python3")
        assert result.endswith("python3")

    def test_unknown_command_returns_as_is(self):
        tools = _import("tools")
        assert tools._resolve_command_path("nonexistent_cmd_xyz_999") == "nonexistent_cmd_xyz_999"


class TestGetTools:
    """get_tools loads MCP clients from mcp.json."""

    def test_no_mcp_json_returns_empty(self):
        tools = _import("tools")
        # The template directory has no mcp.json by default
        mcp_path = Path(_TEMPLATE_DIR) / "mcp.json"
        assert not mcp_path.exists()
        assert tools.get_tools() == []

    def test_empty_servers_returns_empty(self, tmp_path, monkeypatch):
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(json.dumps({"mcpServers": {}}))
        # Point the module at our tmp mcp.json
        monkeypatch.setattr(Path, "parent", property(lambda self: tmp_path), raising=False)
        # Simpler: just write mcp.json into template dir, then clean up
        real_mcp = Path(_TEMPLATE_DIR) / "mcp.json"
        real_mcp.write_text(json.dumps({"mcpServers": {}}))
        try:
            tools = _import("tools")
            assert tools.get_tools() == []
        finally:
            real_mcp.unlink()

    def test_stdio_server_creates_client(self):
        real_mcp = Path(_TEMPLATE_DIR) / "mcp.json"
        real_mcp.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "test-server": {
                            "command": "python3",
                            "args": ["-m", "http.server"],
                        }
                    }
                }
            )
        )
        try:
            tools = _import("tools")
            from strands.tools.mcp import MCPClient

            clients = tools.get_tools()
            assert len(clients) == 1
            assert isinstance(clients[0], MCPClient)
        finally:
            real_mcp.unlink()

    def test_http_server_creates_client(self):
        real_mcp = Path(_TEMPLATE_DIR) / "mcp.json"
        real_mcp.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "remote": {
                            "url": "https://example.com/mcp",
                        }
                    }
                }
            )
        )
        try:
            tools = _import("tools")
            from strands.tools.mcp import MCPClient

            clients = tools.get_tools()
            assert len(clients) == 1
            assert isinstance(clients[0], MCPClient)
        finally:
            real_mcp.unlink()


# ── memory.py ────────────────────────────────────────────────────────────


class TestGetMemory:
    """get_memory returns None when memory is not configured."""

    def test_no_memory_id_returns_none(self, monkeypatch):
        monkeypatch.delenv("MEMORY_ID", raising=False)
        memory = _import("memory")
        assert memory.get_memory("session-1", "user-1") is None

    def test_empty_session_id_returns_none(self, monkeypatch):
        monkeypatch.setenv("MEMORY_ID", "mem-123456789012")
        memory = _import("memory")
        assert memory.get_memory("", "user-1") is None


# ── agent.py handler ─────────────────────────────────────────────────────


async def _collect(agen):
    return [item async for item in agen]


class TestHandler:
    """Handler edge cases that don't require a model call."""

    def test_handler_is_async_generator(self):
        agent = _import("agent")
        result = agent.handler({"prompt": ""})
        assert hasattr(result, "__aiter__")

    def test_empty_prompt_yields_please_send(self):
        agent = _import("agent")
        loop = asyncio.new_event_loop()
        results = loop.run_until_complete(_collect(agent.handler({"prompt": ""})))
        assert results == [{"message": "Please send a message."}]

    def test_missing_prompt_key_yields_please_send(self):
        agent = _import("agent")
        results = asyncio.new_event_loop().run_until_complete(_collect(agent.handler({})))
        assert results == [{"message": "Please send a message."}]

    def test_message_key_also_accepted(self):
        """'message' is an alias for 'prompt' — empty still yields please-send."""
        agent = _import("agent")
        results = asyncio.new_event_loop().run_until_complete(
            _collect(agent.handler({"message": ""}))
        )
        assert results == [{"message": "Please send a message."}]
