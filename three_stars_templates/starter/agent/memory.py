"""AgentCore Memory session management for three-stars agents.

Provides conversation history via AgentCore Memory (short-term memory).
When ``MEMORY_ID`` is set in the environment, the agent preserves context
across turns within a session.  Without it the agent runs stateless.
"""

import os


def get_memory(session_id: str, actor_id: str):
    """Return a session manager that persists conversation history.

    Trigger: ``MEMORY_ID`` environment variable is set.
    Returns None when memory is not configured or session_id is empty,
    letting the agent run without conversation history (stateless mode).

    The returned object is a Strands ``SessionManager`` backed by AgentCore
    Memory.  It automatically loads prior turns on agent init and persists
    each new message as it is added.
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
