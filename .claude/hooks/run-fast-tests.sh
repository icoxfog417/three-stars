#!/bin/bash
INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Only run for Python source or test files
case "$FILE" in
  */tests/integration/*) exit 0 ;;
  */three_stars/*.py|*/tests/test_*.py|*/tests/resources/test_*.py) ;;
  *) exit 0 ;;
esac

cd "$CLAUDE_PROJECT_DIR"
uv run pytest tests/ --ignore=tests/integration -x -q 2>&1 | tail -5
