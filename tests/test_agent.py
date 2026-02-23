"""Tests for the starter agent template (handler, streaming, memory).

These tests exercise the agent.py handler, memory.py session management,
and streaming response behavior using mocked Strands Agent and
bedrock_agentcore SDK.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_TEMPLATE_DIR = str(Path(__file__).resolve().parent.parent / "templates" / "starter" / "agent")


@pytest.fixture(autouse=True)
def _patch_agent_deps():
    """Stub out heavy runtime deps that aren't installed in the test env."""
    fake_runtime = MagicMock()
    fake_app = MagicMock()
    # Make @app.entrypoint a pass-through decorator
    fake_app.entrypoint = lambda fn: fn
    fake_runtime.BedrockAgentCoreApp.return_value = fake_app
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

    fake_strands = MagicMock()
    sys.modules.setdefault("strands", fake_strands)
    sys.modules.setdefault("strands.models", MagicMock())
    sys.modules.setdefault("strands.tools", MagicMock())
    sys.modules.setdefault("strands.tools.mcp", MagicMock())


def _import_agent_module():
    """Import (or reimport) the starter agent module."""
    for mod_name in ("agent", "tools", "memory"):
        sys.modules.pop(mod_name, None)
    if _TEMPLATE_DIR not in sys.path:
        sys.path.insert(0, _TEMPLATE_DIR)
    mod = importlib.import_module("agent")
    return mod


def _cleanup_agent_module():
    """Clean up sys.path and sys.modules after agent import."""
    if _TEMPLATE_DIR in sys.path:
        sys.path.remove(_TEMPLATE_DIR)
    for mod_name in ("agent", "tools", "memory"):
        sys.modules.pop(mod_name, None)


@pytest.fixture()
def agent_module():
    """Import (or reimport) the starter agent module."""
    mod = _import_agent_module()
    yield mod
    _cleanup_agent_module()


@pytest.fixture()
def memory_module():
    """Import (or reimport) the memory module."""
    sys.modules.pop("memory", None)
    if _TEMPLATE_DIR not in sys.path:
        sys.path.insert(0, _TEMPLATE_DIR)
    mod = importlib.import_module("memory")
    yield mod
    if _TEMPLATE_DIR in sys.path:
        sys.path.remove(_TEMPLATE_DIR)
    sys.modules.pop("memory", None)


async def _collect_async(agen):
    """Collect all items from an async generator."""
    items = []
    async for item in agen:
        items.append(item)
    return items


# ── Handler Tests ─────────────────────────────────────────────────────────


class TestHandlerInvocation:
    """Handler runs with a sample event."""

    def test_handler_returns_async_generator(self, agent_module):
        """handler() should return an async generator."""
        result = agent_module.handler({"prompt": "hello"})
        assert hasattr(result, "__aiter__")

    def test_handler_empty_prompt_yields_message(self, agent_module):
        """Empty prompt should yield a 'Please send a message.' response."""
        gen = agent_module.handler({"prompt": ""})
        results = asyncio.new_event_loop().run_until_complete(_collect_async(gen))
        assert len(results) == 1
        assert results[0]["message"] == "Please send a message."

    def test_handler_no_prompt_key_yields_message(self, agent_module):
        """Missing prompt key should yield a 'Please send a message.' response."""
        gen = agent_module.handler({})
        results = asyncio.new_event_loop().run_until_complete(_collect_async(gen))
        assert len(results) == 1
        assert results[0]["message"] == "Please send a message."


class TestStreamingResponse:
    """Async generator yields valid chunks from the Strands Agent."""

    def test_streaming_yields_data_events(self):
        """Handler should yield data events from the Strands Agent stream."""
        mock_agent = MagicMock()

        async def mock_stream(msg):
            yield {"data": "Hello "}
            yield {"data": "world"}

        mock_agent.stream_async = mock_stream

        # Import agent, then patch Agent class on the module
        mod = _import_agent_module()
        try:
            with patch.object(mod, "Agent", return_value=mock_agent):
                gen = mod.handler({"prompt": "hi", "session_id": "s1"})
                results = asyncio.new_event_loop().run_until_complete(_collect_async(gen))
        finally:
            _cleanup_agent_module()

        assert len(results) == 2
        assert results[0] == {"data": "Hello "}
        assert results[1] == {"data": "world"}

    def test_streaming_skips_result_events(self):
        """Handler should skip events containing 'result' key."""
        mock_agent = MagicMock()

        async def mock_stream(msg):
            yield {"data": "token"}
            yield {"result": "final"}  # Should be skipped

        mock_agent.stream_async = mock_stream

        mod = _import_agent_module()
        try:
            with patch.object(mod, "Agent", return_value=mock_agent):
                gen = mod.handler({"prompt": "hi"})
                results = asyncio.new_event_loop().run_until_complete(_collect_async(gen))
        finally:
            _cleanup_agent_module()

        assert len(results) == 1
        assert results[0] == {"data": "token"}


# ── Memory Tests ──────────────────────────────────────────────────────────


class TestMemorySession:
    """Memory stores/recalls by session."""

    def test_returns_none_without_memory_id(self, memory_module, monkeypatch):
        """Without MEMORY_ID env var, get_memory returns None."""
        monkeypatch.delenv("MEMORY_ID", raising=False)
        result = memory_module.get_memory("session-1", "user-1")
        assert result is None

    def test_returns_none_without_session_id(self, memory_module, monkeypatch):
        """Empty session_id returns None even with MEMORY_ID."""
        monkeypatch.setenv("MEMORY_ID", "mem-123")
        result = memory_module.get_memory("", "user-1")
        assert result is None

    def test_returns_session_manager_with_config(self, memory_module, monkeypatch):
        """With MEMORY_ID and session_id, returns AgentCoreMemorySessionManager."""
        monkeypatch.setenv("MEMORY_ID", "mem-123")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

        result = memory_module.get_memory("session-1", "user-1")
        # Result is a MagicMock from the sys.modules stub — key check is
        # that the code path executed (not None).
        assert result is not None


# ── Handler Error Tests ───────────────────────────────────────────────────


class TestHandlerError:
    """Handler returns error response on failure."""

    def test_handler_propagates_agent_error(self):
        """If the Strands Agent raises, the error propagates."""
        mock_agent = MagicMock()

        async def mock_stream(msg):
            raise RuntimeError("Model API error")
            yield  # noqa: RUF027 — unreachable, but makes this an async generator

        mock_agent.stream_async = mock_stream

        mod = _import_agent_module()
        try:
            with patch.object(mod, "Agent", return_value=mock_agent):
                gen = mod.handler({"prompt": "hi"})
                with pytest.raises(RuntimeError, match="Model API error"):
                    asyncio.new_event_loop().run_until_complete(_collect_async(gen))
        finally:
            _cleanup_agent_module()
