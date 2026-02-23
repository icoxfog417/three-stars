"""Starter agent for three-stars.

This agent receives messages from the frontend via the /api/invoke endpoint
and responds using a Strands Agent powered by Amazon Bedrock.

The handler is an async generator — BedrockAgentCoreApp wraps it in an SSE
StreamingResponse so tokens arrive at the client as they are produced.

Conversation history is automatically preserved within a session using
AgentCore Memory (short-term memory).  Set MEMORY_ID to enable it.

Customize this file with your agent logic — add tools, change the model,
or adjust the system prompt.
"""

import json
import os
import re
from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

app = BedrockAgentCoreApp()


def _resolve_env_refs(value: str) -> str:
    """Replace ${VAR} references in a string with os.environ values."""
    return re.sub(
        r"\$\{([^}]+)\}",
        lambda m: os.environ.get(m.group(1), ""),
        value,
    )


def _load_mcp_clients() -> list:
    """Load MCP tool clients from agent/mcp.json if it exists.

    Each server entry becomes an MCPClient. Supports two transport types:

    - **stdio** (has ``command``): spawns a subprocess via ``stdio_client``
    - **http** (has ``url``): connects via ``streamablehttp_client``

    Environment variable references (``${VAR}``) in ``env``, ``args``,
    ``command``, and ``url`` fields are resolved from ``os.environ``.
    AWS credentials from the current boto3 session are forwarded to
    stdio subprocesses automatically.
    """
    mcp_path = Path(__file__).parent / "mcp.json"
    if not mcp_path.exists():
        return []

    with open(mcp_path) as f:
        mcp_config = json.load(f)

    servers = mcp_config.get("mcpServers", {})
    if not servers:
        return []

    from strands.tools.mcp import MCPClient

    # Build AWS credential env from the current boto3 session (for stdio)
    import boto3

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
    for _name, server in servers.items():
        if "command" in server:
            client = _make_stdio_client(MCPClient, server, aws_env)
        elif "url" in server:
            client = _make_http_client(MCPClient, server)
        else:
            continue
        clients.append(client)

    return clients


def _make_stdio_client(MCPClient, server: dict, aws_env: dict[str, str]):
    """Create an MCPClient for a stdio (command-based) MCP server."""
    from mcp.client.stdio import StdioServerParameters, stdio_client

    command = _resolve_env_refs(server["command"])
    args = [_resolve_env_refs(a) for a in server.get("args", [])]

    env = {**aws_env}
    for k, v in server.get("env", {}).items():
        env[k] = _resolve_env_refs(v)

    return MCPClient(
        lambda: stdio_client(StdioServerParameters(command=command, args=args, env=env))
    )


def _make_http_client(MCPClient, server: dict):
    """Create an MCPClient for an HTTP (url-based) MCP server."""
    from mcp.client.streamable_http import streamablehttp_client

    url = _resolve_env_refs(server["url"])
    headers: dict[str, str] = {}
    for k, v in server.get("headers", {}).items():
        headers[k] = _resolve_env_refs(v)

    return MCPClient(lambda: streamablehttp_client(url=url, headers=headers or None))


def _build_session_manager(session_id: str, actor_id: str):
    """Create an AgentCoreMemorySessionManager if MEMORY_ID is configured.

    Returns None when memory is not available, letting the agent run
    without conversation history (stateless mode).
    """
    memory_id = os.environ.get("MEMORY_ID", "")
    if not memory_id or not session_id:
        return None

    from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
    from bedrock_agentcore.memory.integrations.strands.session_manager import (
        AgentCoreMemorySessionManager,
    )

    config = AgentCoreMemoryConfig(
        memory_id=memory_id,
        session_id=session_id,
        actor_id=actor_id,
    )
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    return AgentCoreMemorySessionManager(config, region_name=region)


model_id = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
model = BedrockModel(model_id=model_id, region_name=region)

tools = _load_mcp_clients()


@app.entrypoint
async def handler(payload):
    """Handle incoming requests from the frontend.

    Yields streaming events from the Strands Agent. BedrockAgentCoreApp
    detects the async generator and wraps it in an SSE StreamingResponse.

    Args:
        payload: Request payload dict with 'prompt' or 'message' field.
    """
    user_message = payload.get("prompt") or payload.get("message", "")
    session_id = payload.get("session_id", "")
    actor_id = payload.get("actor_id", "default-user")

    if not user_message:
        yield {"message": "Please send a message."}
        return

    session_manager = _build_session_manager(session_id, actor_id)

    agent = Agent(
        model=model,
        system_prompt="You are a helpful AI assistant. Be concise and friendly.",
        tools=tools or None,
        session_manager=session_manager,
    )

    stream = agent.stream_async(user_message)
    async for event in stream:
        if not isinstance(event, dict):
            continue
        # Skip the final result event (contains non-serializable AgentResult)
        if "result" in event:
            continue
        # Extract text delta from streaming chunks
        text = (event.get("data")
                or (event.get("delta") or {}).get("text")
                or "")
        if text:
            yield {"data": text}


if __name__ == "__main__":
    app.run()
