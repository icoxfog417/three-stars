# Proposal: test-manager Skill and Test Hooks

**Date**: 2026-02-23
**Author**: Claude Agent
**Status**: Proposed

## Background

The current test suite has gaps: orchestration is untested, some resources lack CRUD coverage, and there's no mechanism to ensure new resources get proper tests. We need:

1. A `test-manager` skill that defines test standards per feature type and implements missing tests
2. Hooks that automatically check test coverage when resource code is modified

## The Test Pyramid (for this project)

```
tests/
├── conftest.py
├── test_config.py              # Pure logic
├── test_state.py               # Pure logic
├── test_storage.py             # Resource contract
├── test_cdn.py                 # Resource contract
├── test_edge.py                # Resource contract (NEW)
├── test_agentcore.py           # Resource contract (REWRITE — mock-based)
├── test_deploy.py              # Orchestration (NEW)
├── test_destroy.py             # Orchestration (NEW)
├── test_mcp.py                 # Template tools
├── test_agent.py               # Template handler (NEW)
│
└── integration/
    ├── __init__.py
    ├── conftest.py             # Real AWS fixtures
    └── test_agentcore.py       # Current real-AWS tests (MOVE)
```

Run commands:
```bash
uv run pytest tests/ --ignore=tests/integration    # Fast, every commit
uv run pytest tests/integration                     # Slow, CI/nightly
```

## Test Standards by Feature Type

### 1. Resource Tests (`test_{resource}.py`)

Every resource module (storage, cdn, edge, agentcore) MUST have:

| Test | What it proves |
|------|----------------|
| `test_deploy_returns_state` | `deploy()` returns correct state fields |
| `test_deploy_update_idempotent` | Re-deploy with existing state doesn't recreate |
| `test_deploy_error_handling` | Handles permission denied, quota, timeout |
| `test_destroy_cleans_up` | `destroy()` removes resources |
| `test_destroy_idempotent` | `destroy()` on already-deleted is no-op |
| `test_get_status` | `get_status()` returns correct status |

Mock strategy: `@mock_aws` (moto) for S3/CloudFront/Lambda/IAM. `unittest.mock.patch` for AgentCore (no moto support). Tests verify **our code's logic** (correct API calls, state returned, error handling), not AWS behavior.

### 2. Orchestration Tests (`test_deploy.py`, `test_destroy.py`)

Mock all resource modules, test only the orchestrator:

| Test | What it proves |
|------|----------------|
| `test_deploy_step_order` | Storage → AgentCore → Edge → CDN → ResourcePolicy |
| `test_deploy_state_after_each_step` | State file saved after each resource |
| `test_deploy_partial_failure` | Step N fails → state has steps 1..N-1 |
| `test_deploy_update_detection` | Existing state triggers update path |
| `test_deploy_force_flag` | Force flag triggers fresh create |
| `test_destroy_step_order` | CDN disassociate → Edge → parallel cleanup |
| `test_destroy_state_cleanup` | State file removed on success |
| `test_destroy_partial_failure` | Continues cleanup after individual failures |

Mock strategy: Pure `unittest.mock.patch` — no moto needed. Each resource module is patched entirely.

### 3. Template Tests (`test_agent.py`, `test_mcp.py`)

| Test | What it proves |
|------|----------------|
| `test_handler_invocation` | Handler runs with sample event |
| `test_streaming_response` | Async generator yields valid chunks |
| `test_tool_loading` | MCP tools loaded from config |
| `test_memory_session` | Memory stores/recalls by session |
| `test_handler_error` | Handler returns error response on failure |

Mock strategy: `sys.modules` stub for `bedrock_agentcore` (not installable locally). Mock Strands Agent to avoid model calls.

## test-manager Skill

### Purpose

Invoked with `/test-manager` when:
- Implementing a new resource module
- Adding orchestration steps
- Modifying agent templates
- Reviewing test coverage gaps

### Behavior

1. **Scan** — identify which feature type changed (resource / orchestration / template)
2. **Check** — compare existing tests against the required checklist above
3. **Report** — list missing tests with specific function signatures
4. **Implement** — write the missing tests (or ask user which to prioritize)

### Skill File

Location: `.claude/skills/test-manager/SKILL.md`

## Hooks

### Hook 1: Test Structure Check (Stop hook)

When Claude finishes a response that edited resource/orchestration code, verify tests exist.

**File: `.claude/settings.json`** (project-level, committed to git)

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "prompt",
            "prompt": "Check if Claude edited any files in three_stars/resources/ or three_stars/commands/ during this conversation turn. If yes, verify that corresponding test files in tests/ cover the 6 resource test cases or orchestration test cases defined in .claude/skills/test-manager/SKILL.md. If tests are missing, respond with a reminder listing which tests need to be added. If no resource/command files were edited, or tests already exist, respond with nothing.",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

**Why Stop hook**: It fires after Claude finishes responding. It won't block work but reminds about missing tests before the conversation moves on. Low friction, high visibility.

### Hook 2: Run Fast Tests After Edits (PostToolUse)

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/run-fast-tests.sh",
            "timeout": 60,
            "async": true,
            "statusMessage": "Running tests..."
          }
        ]
      }
    ]
  }
}
```

**File: `.claude/hooks/run-fast-tests.sh`**

```bash
#!/bin/bash
INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Only run for Python source or test files
case "$FILE" in
  */three_stars/*.py|*/tests/test_*.py) ;;
  *) exit 0 ;;
esac

cd "$CLAUDE_PROJECT_DIR"
uv run pytest tests/ --ignore=tests/integration -x -q 2>&1 | tail -5
```

**Why async PostToolUse**: Tests run in background after each edit. Doesn't block Claude's work. If tests fail, the output appears as context.

## Relationship Between Skills

```
┌─────────────────────────────────────────┐
│  Development Flow                       │
│                                         │
│  1. Write resource code                 │
│     └─ Hook: run-fast-tests (async)     │
│                                         │
│  2. Claude stops responding             │
│     └─ Hook: test structure check       │
│        └─ "Missing: test_destroy_       │
│            idempotent for edge.py"       │
│                                         │
│  3. /test-manager                       │
│     └─ Scan, check, implement tests     │
│                                         │
│  4. /test-dx                            │
│     └─ Full E2E with real AWS           │
└─────────────────────────────────────────┘
```

## Impact

- **Requirements**: No change
- **Design**: No change
- **Tasks**: Add task for test-manager skill implementation and hook setup

## Alternatives Considered

1. **PreToolUse hook to block edits without tests** — Too aggressive; would block work-in-progress
2. **Git pre-commit hook** — Runs outside Claude Code context; can't suggest fixes
3. **TaskCompleted hook** — Only fires on task tool completion; not always used

## Implementation Plan

1. Create `.claude/skills/test-manager/SKILL.md` with test checklists and scan logic
2. Create `.claude/hooks/run-fast-tests.sh`
3. Add hooks configuration to `.claude/settings.json`
4. Create `tests/integration/` directory and move AgentCore integration tests
5. Implement missing test files: `test_edge.py`, `test_deploy.py`, `test_destroy.py`, `test_agent.py`
6. Rewrite `test_agentcore.py` as mock-based contract tests
