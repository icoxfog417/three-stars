# Proposal: AgentCore Memory for Conversation History

**Date**: 2026-02-23
**Author**: Claude Agent
**Status**: Implemented

## Background

Currently, three-stars agents are **stateless** — every message is treated as a fresh interaction with no knowledge of prior conversation turns. The Lambda@Edge function generates a new `session_id` per request (or uses one from the payload), but this ID is only passed as a header to AgentCore and never used to persist or retrieve conversation history. The Strands `Agent` instance in the starter template holds no cross-request state either, since each AgentCore invocation runs in an isolated context.

This means users cannot have multi-turn conversations. An agent that was just told "My name is Alice" will not remember that name in the next message. This is a fundamental gap for any conversational AI application.

**Amazon Bedrock AgentCore Memory** solves this with a fully managed memory service. Short-term memory stores raw conversation events scoped by actor and session, allowing the agent to reload full conversation context on each invocation. The Strands Agents SDK has native integration via `AgentCoreMemorySessionManager`, making adoption straightforward.

## Proposal

Add AgentCore Memory support to three-stars so that deployed agents automatically preserve conversation history within a session using **short-term memory (STM)**.

### Scope

- **In scope**: Short-term memory (per-session conversation history) enabled by default
- **Out of scope (future work)**: Long-term memory strategies (semantic, preferences, summaries), user-configurable memory strategies, memory resource management CLI commands

### Changes Overview

The feature touches four layers:

1. **Infrastructure** (`resources/agentcore.py`) — Create a Memory resource during deployment
2. **Agent template** (`templates/starter/agent/agent.py`) — Integrate `AgentCoreMemorySessionManager` with the Strands Agent
3. **Edge function** (`resources/edge.py`) — Pass `session_id` from frontend payload to the agent payload (already partially done)
4. **Frontend template** (`templates/starter/app/index.html`) — Generate and maintain a `session_id` per browser session

### 1. Infrastructure: Memory Resource Lifecycle

**Deploy** — After creating the AgentCore runtime, create a Memory resource:

```python
# In resources/agentcore.py deploy()
memory = agentcore_client.create_memory(
    name=names.memory,           # e.g. "{project}-memory"
    description=f"Conversation memory for {config.name}",
)
# Poll until ACTIVE (similar to runtime polling)
```

**State** — Add `memory_id` and `memory_name` to `AgentCoreState`:

```python
@dataclass
class AgentCoreState:
    # ... existing fields ...
    memory_id: str | None = None
    memory_name: str | None = None
```

**Destroy** — Delete the Memory resource during teardown (before deleting the runtime).

**Naming** — Add `memory` field to `ResourceNames`:

```python
@dataclass(frozen=True)
class ResourceNames:
    # ... existing fields ...
    memory: str  # "{project_name}-memory"
```

**Environment** — Pass `MEMORY_ID` to the agent runtime as an environment variable or embed it in the agent code during packaging (same pattern as `BEDROCK_MODEL_ID`).

### 2. Agent Template: Strands Memory Integration

Update `templates/starter/agent/agent.py` to use `AgentCoreMemorySessionManager`:

```python
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)

MEMORY_ID = os.environ.get("MEMORY_ID", "")

@app.entrypoint
async def handler(payload):
    user_message = payload.get("prompt") or payload.get("message", "")
    session_id = payload.get("session_id", "")
    # Use session_id as actor_id for simplicity (one actor per session)
    actor_id = payload.get("actor_id", "default-user")

    if not user_message:
        yield {"message": "Please send a message."}
        return

    if MEMORY_ID and session_id:
        config = AgentCoreMemoryConfig(
            memory_id=MEMORY_ID,
            session_id=session_id,
            actor_id=actor_id,
        )
        session_manager = AgentCoreMemorySessionManager(config, region_name=region)
    else:
        session_manager = None

    request_agent = Agent(
        model=model,
        system_prompt="You are a helpful AI assistant. Be concise and friendly.",
        tools=tools or None,
        session_manager=session_manager,
    )

    stream = request_agent.stream_async(user_message)
    async for event in stream:
        # ... existing streaming logic ...
```

Key design decisions:
- **Agent-per-request**: Create a new `Agent` instance per request with the session manager, so conversation history is loaded fresh from memory each time. The module-level `agent` is removed.
- **Graceful fallback**: If `MEMORY_ID` is not set (e.g. older deployments), the agent works without memory (current behavior).
- **`actor_id`**: Default to `"default-user"`. Future work could use authenticated user IDs.

### 3. Edge Function: Forward session_id in Payload

The Lambda@Edge function already extracts `session_id` from the request body and passes it as a header. We need to also ensure `session_id` is included in the **payload body** forwarded to AgentCore, so the agent handler can read it:

```python
# In edge function code (embedded in edge.py)
try:
    parsed = json.loads(body_bytes.decode("utf-8"))
    session_id = parsed.get("session_id") or str(uuid.uuid4())
    # Ensure session_id is in the forwarded payload
    parsed["session_id"] = session_id
    body_bytes = json.dumps(parsed).encode("utf-8")
except Exception:
    session_id = str(uuid.uuid4())
```

This is a minimal change — we just write the resolved `session_id` back into the payload so the agent can read it without parsing headers.

### 4. Frontend: Session Management

Update `templates/starter/app/index.html` to generate and persist a `session_id`:

```javascript
// Generate a session ID per browser tab/session
const sessionId = crypto.randomUUID();

async function sendMessage() {
    // ...
    const resp = await fetch('/api/invoke', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            message: text,
            session_id: sessionId,
        }),
    });
    // ...
}
```

Using `crypto.randomUUID()` gives each browser tab its own conversation session. Refreshing the page starts a new session (appropriate default behavior).

## Impact

### Requirements (`requirements.md`)
- Add functional requirement: "Agents preserve conversation context within a session using AgentCore Memory"
- Add to non-functional requirements: "Memory resource created/destroyed as part of deployment lifecycle"

### Design (`design.md`)
- Add Memory resource to deployment sequence (step between runtime creation and edge function)
- Add `memory_id` and `memory_name` to `AgentCoreState`
- Add `memory` to `ResourceNames`
- Document agent-per-request pattern with session manager

### Tasks (`tasks.md`)
- New sprint with ~6 tasks (see Implementation Plan below)

## Alternatives Considered

### 1. DynamoDB-based conversation storage
Manual implementation with DynamoDB table for storing messages. Rejected because AgentCore Memory is purpose-built, fully managed, and integrates natively with Strands SDK. It also provides a path to long-term memory strategies without additional infrastructure.

### 2. Agent-level in-memory state
Keep conversation history in the Strands Agent's built-in message buffer. Rejected because AgentCore runtime invocations are stateless — the process may not persist between requests, so in-memory state would be lost.

### 3. Long-term memory enabled by default
Include semantic/summary/preference strategies from the start. Rejected to keep the initial implementation simple and predictable. Long-term memory runs asynchronous extraction pipelines and requires more configuration. It can be added as a follow-up feature.

### 4. Configurable memory in `three-stars.yml`
Add `agent.memory` config section. Deferred — the default short-term-only setup works well for the common case. Configuration can be added when long-term memory support is introduced.

## Implementation Plan

### Sprint 7: AgentCore Memory — Conversation History

**Goal**: Deployed agents remember conversation history within a session
**Deliverable**: Multi-turn conversations work out of the box

| Task | Description |
|------|-------------|
| T61 | Add `memory` to `ResourceNames` and `memory_id`/`memory_name` to `AgentCoreState` |
| T62 | Implement Memory resource create/delete/status in `resources/agentcore.py` |
| T63 | Pass `MEMORY_ID` to agent runtime (via environment config or code embedding) |
| T64 | Update `templates/starter/agent/agent.py` — integrate `AgentCoreMemorySessionManager`, agent-per-request pattern |
| T65 | Update Lambda@Edge code to write resolved `session_id` back into forwarded payload |
| T66 | Update `templates/starter/app/index.html` — generate `session_id` per tab, send with each message |
| T67 | Update tests — mock Memory API calls, verify state serialization |
| T68 | Update spec files (`requirements.md`, `design.md`, `tasks.md`) |

### Dependencies

- `bedrock-agentcore` SDK already in `requirements.txt` — Memory client is included
- No new infrastructure dependencies (Memory is part of AgentCore service)
- Backwards compatible: existing deployments without memory continue to work (graceful fallback in agent template)
