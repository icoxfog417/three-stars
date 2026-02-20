---
name: kickoff
description: Generate a kickoff briefing with full context for starting work on a sprint unit or task. Use when beginning work on a unit, task, or feature.
argument-hint: [unit name or task description]
---

Generate a kickoff briefing for starting work on: $ARGUMENTS

## Process

1. Find the unit/task in `spec/tasks.md`
2. Extract tasks, files to modify, and dependencies
3. Check `spec/implementation_qa.md` for related verified Q&A entries
4. Read related proposals in `spec/proposals/`
5. Check `spec/design.md` for relevant architecture details

## Pre-Kickoff Verification

Before generating the briefing, check and report:

- **Dependencies met?**: Prerequisite units should be ✅ DONE
- **No blockers?**: No 🚫 BLOCKED status on related tasks
- **Q&A ready?**: Related technical questions should be ✅ Verified
- **Proposal exists?**: Related proposal should be Approved

If any check fails, flag it clearly in the briefing.

## Briefing Format

### Unit {X}: {Title}

#### Context
- **Sprint**: {N}
- **Phase**: {Phase name}
- **Dependencies**: {Units that must complete first, or "None"}
- **Proposal**: `spec/proposals/{related}.md`

#### Tasks
- [ ] Task 1
- [ ] Task 2

#### Files to Create/Modify
| File | Action | Notes |
|------|--------|-------|
| path/to/file | Create/Modify | Description |

#### Related Resources
- Q&A: See Q{N} in `spec/implementation_qa.md`
- Pattern reference: `{path to similar implementation}`
- Design reference: See {section} in `spec/design.md`

#### Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2

#### Quick Start
1. First command or action
2. Second step

## After Completion

1. Mark tasks as done in `spec/tasks.md` (change `⬜` to `✅`)
2. Update "Last Updated" date in tasks.md
3. Create PR if feature is complete
4. Update `spec/implementation_qa.md` if new findings discovered
