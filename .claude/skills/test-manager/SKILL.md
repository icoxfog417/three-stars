# test-manager Skill

Manage test structure and implement missing tests for three-stars. Invoke with `/test-manager` when implementing new resource modules, adding orchestration steps, modifying agent templates, or reviewing test coverage gaps.

## Test Organization

```
tests/
├── conftest.py                 # Shared fixtures (dummy AWS creds, sample config, project_dir)
├── test_config.py              # Pure logic — config loading, validation
├── test_state.py               # Pure logic — state serialization, backup
├── test_storage.py             # Resource contract tests (moto)
├── test_cdn.py                 # Resource contract tests (moto)
├── test_edge.py                # Resource contract tests (moto)
├── test_agentcore.py           # Resource contract tests (mock — no moto support)
├── test_deploy.py              # Orchestration tests (pure mock)
├── test_destroy.py             # Orchestration tests (pure mock)
├── test_mcp.py                 # Template tools tests
├── test_agent.py               # Template handler tests
│
└── integration/
    ├── __init__.py
    ├── conftest.py             # Real AWS fixtures
    └── test_agentcore.py       # Real AWS lifecycle tests
```

### Run Commands

```bash
uv run pytest tests/ --ignore=tests/integration    # Fast, every commit
uv run pytest tests/integration                     # Slow, CI/nightly
```

## Test Standards by Feature Type

### 1. Resource Tests (`test_{resource}.py`)

Every resource module (storage, cdn, edge, agentcore) MUST have these 6 tests:

| Test | What it proves |
|------|----------------|
| `test_deploy_returns_state` | `deploy()` returns correct state fields |
| `test_deploy_update_idempotent` | Re-deploy with existing state doesn't recreate |
| `test_deploy_error_handling` | Handles permission denied, quota, timeout |
| `test_destroy_cleans_up` | `destroy()` removes resources |
| `test_destroy_idempotent` | `destroy()` on already-deleted is no-op |
| `test_get_status` | `get_status()` returns correct status |

**Mock strategy**:
- `@mock_aws` (moto) for S3, CloudFront, Lambda, IAM
- `unittest.mock.patch` for AgentCore (no moto support)
- Tests verify **our code's logic** (correct API calls, state shape, error handling), NOT AWS behavior

### 2. Orchestration Tests (`test_deploy.py`, `test_destroy.py`)

Mock all resource modules, test only the orchestrator:

| Test | What it proves |
|------|----------------|
| `test_deploy_step_order` | Storage → AgentCore → Edge → CDN → ResourcePolicy |
| `test_deploy_state_after_each_step` | State file saved after each resource |
| `test_deploy_partial_failure` | Step N fails → state has steps 1..N-1 |
| `test_deploy_update_detection` | Existing state triggers update path |
| `test_deploy_force_flag` | Force flag triggers fresh create |
| `test_destroy_step_order` | Edge disassociation → Edge delete → AgentCore → S3 → CDN |
| `test_destroy_state_cleanup` | State file removed on success |
| `test_destroy_partial_failure` | Continues cleanup after individual failures |

**Mock strategy**: Pure `unittest.mock.patch` — no moto needed. Each resource module is patched entirely.

### 3. Template Tests (`test_agent.py`, `test_mcp.py`)

| Test | What it proves |
|------|----------------|
| `test_handler_invocation` | Handler runs with sample event |
| `test_streaming_response` | Async generator yields valid chunks |
| `test_tool_loading` | MCP tools loaded from config |
| `test_memory_session` | Memory stores/recalls by session |
| `test_handler_error` | Handler returns error response on failure |

**Mock strategy**: `sys.modules` stub for `bedrock_agentcore` (not installable locally). Mock Strands Agent to avoid model calls.

## Workflow

When invoked, follow this procedure:

1. **Scan** — identify which feature type changed (resource / orchestration / template)
   - Check `three_stars/resources/` for resource modules
   - Check `three_stars/deploy.py` and `three_stars/destroy.py` for orchestration
   - Check `templates/starter/agent/` for template code

2. **Check** — compare existing tests against the required checklist above
   - For each resource module, verify the 6 required test functions exist
   - For orchestration, verify the 8 required test functions exist
   - For templates, verify the 5 required test functions exist

3. **Report** — list missing tests with specific function signatures

4. **Implement** — write the missing tests (or ask user which to prioritize)

## Key Principles

- **Test your code, not AWS**: Mocks verify correct API calls and state handling, not AWS behavior
- **Contract tests**: Each resource's `deploy()` must return the correct state type with all fields populated
- **Orchestration isolation**: Deploy/destroy tests mock all resource modules completely
- **Fast by default**: All tests under `tests/` (excluding `integration/`) should run in < 10 seconds
