---
name: release
description: Manage a PyPI release — bump version, create git tag, and create a GitHub release with auto-generated notes. Use when publishing a new version.
argument-hint: <patch|minor|major> or specific version like 0.2.0
---

Perform a release for three-stars: $ARGUMENTS

## Process

### Step 1: Determine the new version

- Read the current version from `pyproject.toml` (`version = "..."`)
- Parse `$ARGUMENTS` to decide the new version:
  - `patch` → bump patch (e.g. 0.1.2 → 0.1.3)
  - `minor` → bump minor (e.g. 0.1.2 → 0.2.0)
  - `major` → bump major (e.g. 0.1.2 → 1.0.0)
  - A specific version string (e.g. `0.2.0`) → use as-is
- If no argument is provided, default to `patch`.

### Step 2: Pre-flight checks

Run these checks and **stop if any fail**:

1. **Working tree is clean**: `git status --porcelain` must be empty (no uncommitted changes)
2. **On main branch**: Current branch must be `main`
3. **Tests pass**: `uv run pytest --ignore=tests/integration`
4. **Lint passes**: `uv run ruff check three_stars/ tests/`
5. **Tag doesn't exist**: `git tag -l v{NEW_VERSION}` must return empty
6. **gh CLI available**: `gh --version` must succeed

Report all check results. If any check fails, explain what needs to be fixed and stop.

### Step 3: Bump version

- Edit `pyproject.toml` to set the new version
- Commit: `chore: bump version to {NEW_VERSION}`

### Step 4: Create git tag

- Create an annotated tag: `git tag -a v{NEW_VERSION} -m "Release v{NEW_VERSION}"`

### Step 5: Confirm before pushing

Show a summary and **ask the user for confirmation** before proceeding:

```
Release Summary
  Version: {OLD} → {NEW}
  Tag:     v{NEW_VERSION}
  Commits since last tag: (list short log)

This will push the commit and tag to origin, then create a GitHub release
which triggers the PyPI publish workflow.

Proceed? (approve to continue)
```

### Step 6: Push and create GitHub release

After the user confirms:

1. Push the commit and tag: `git push origin main --follow-tags`
2. Create GitHub release with auto-generated notes:
   ```
   gh release create v{NEW_VERSION} --generate-notes --verify-tag
   ```

### Step 7: Post-release report

Show the final status:

- GitHub release URL
- Note that the PyPI publish workflow has been triggered
- Link to the workflow run: `https://github.com/icoxfog417/three-stars/actions`
