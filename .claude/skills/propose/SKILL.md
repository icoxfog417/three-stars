---
name: propose
description: Create a change proposal document before modifying spec files. Use when making significant changes to requirements, design, or tasks.
argument-hint: [feature or change description]
---

Create a change proposal document for: $ARGUMENTS

## Process

1. Read `spec/requirements.md`, `spec/design.md`, and `spec/tasks.md` to understand current project state
2. Check existing proposals in `spec/proposals/` for related prior decisions
3. Create a new proposal file at `spec/proposals/YYYYMMDD_proposal_name.md` using today's date (use snake_case, descriptive but concise)

## Proposal Template

Use this template for the file:

```markdown
# Proposal: {Title}

**Date**: YYYY-MM-DD
**Author**: {Name or "Claude Agent"}
**Status**: Proposed

## Background

{Why this change is needed. 2-3 sentences.}

## Current Behavior

{What happens now. Include code snippets if relevant.}

## Proposal

{Detailed description of the proposed changes.}

### {Sub-section if needed}

{Details, code examples, diagrams.}

## Impact

- **Requirements**: {What changes in requirements.md, or "No change"}
- **Design**: {What changes in design.md, or "No change"}
- **Tasks**: {What new tasks will be added to tasks.md}

## Alternatives Considered

1. **{Alternative 1}**: {Why not chosen}
2. **{Alternative 2}**: {Why not chosen}

## Implementation Plan

1. Step 1
2. Step 2
3. Step 3

## Testing Plan

{How to verify the changes work correctly.}

- Test case 1
- Test case 2
```

## Status Values

| Status | Meaning |
|--------|---------|
| Proposed | Initial draft, awaiting review |
| Approved | Accepted, ready for implementation |
| Implemented | Changes have been made |
| Rejected | Not accepted (keep for record) |

## After Creating

1. Update the relevant spec files as described in the Impact section
2. Reference the proposal in the commit message
3. Update proposal status after implementation
