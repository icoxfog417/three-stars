---
name: sprint
description: Analyze current sprint status, identify parallel work opportunities, and coordinate task dependencies. Use when starting sprint work, checking what can be worked on simultaneously, or reviewing progress.
argument-hint: [optional: sprint number or "status"]
---

Analyze the current sprint and provide a parallel work plan. $ARGUMENTS

Current task status:
!`grep -c '⬜\|🔄\|✅\|🚫\|⏸️' spec/tasks.md 2>/dev/null || echo "No tasks.md found"`

## Process

1. Read `spec/tasks.md` and find the active sprint (look for tasks with ⬜ or 🔄 status)
2. Read `spec/design.md` for architecture context
3. Map work units and their dependency graph

## Output Format

Provide this analysis:

### Sprint {N} Parallel Work Plan

**Completion**: {X}% ({done}/{total} tasks)
**Active Phase**: {Phase name}

#### By Unit
| Unit | Progress | Status | Blocker |
|------|----------|--------|---------|
| A    | {done}/{total} | {status icon} | {blocker or "None"} |

#### Independent Units (can start immediately)
- Unit X: [description] → [files affected]
- Unit Y: [description] → [files affected]

#### Dependent Units (wait for prerequisites)
- Unit Z: Depends on [X, Y] → [files affected]

#### Shared Files (coordinate changes)
- [shared config file] - touched by: [Units]

#### Recommended Parallel Assignments
1. Agent/Dev 1: Unit X + Unit Y (no overlap)
2. Agent/Dev 2: Unit Z (independent)

## File Conflict Safety Check

Evaluate each file/directory touched by active units:

| File/Directory | Safe for Parallel? | Notes |
|----------------|-------------------|-------|
| Shared config files | Coordinate | Central config, merge carefully |
| Database schema | Coordinate | Schema changes need sequencing |
| Independent modules | Usually safe | Different modules can be parallel |
| Isolated functions/services | Safe | Each service is isolated |

## Task Status Reference

| Icon | Status | Meaning |
|------|--------|---------|
| ⬜ | TODO | Not started, available for work |
| 🔄 | IN PROGRESS | Someone working on it |
| ✅ | DONE | Completed |
| 🚫 | BLOCKED | Waiting on dependency |
| ⏸️ | ON HOLD | Paused |
