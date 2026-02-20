---
name: investigate
description: Investigate a technical question using the sandbox verification workflow. Creates sandbox experiments, tests approaches, and documents findings in implementation_qa.md.
argument-hint: [technical question to investigate]
context: fork
allowed-tools: Read, Grep, Glob, Bash, Write, Edit, WebSearch, WebFetch
---

Investigate this technical question: $ARGUMENTS

## Sandbox Verification Workflow

Follow the "Question First" approach:

### Step 1: Document the Question

Add a new entry to `spec/implementation_qa.md` with status ⏳ Pending:

```markdown
### Q{N}: {Question}

**Status**: ⏳ Pending Verification
```

### Step 2: Create Sandbox

Create a minimal sandbox sample to answer the question:

```bash
mkdir -p .sandbox/{descriptive-name}
```

Create a README.md in the sandbox directory explaining what this test verifies.

### Step 3: Build and Test

- Build a minimal reproducible sample
- Test the functionality thoroughly
- Debug and fix issues
- Document unexpected behaviors

### Step 4: Document Answer

Update the entry in `spec/implementation_qa.md` with findings:

```markdown
### Q{N}: {Question}

**Status**: ✅ Verified

**Answer**: {Detailed explanation with context}

**Code Sample**:
\```
// Minimal working example from sandbox
\```

**Verified in**: `.sandbox/{directory}/`

**Key Findings**:
- Important discovery 1
- Important discovery 2

**Gotchas**:
- Potential issue to watch for
- Common mistake to avoid

**References**:
- [Official Documentation URL]
- [Related Q&A entries]
```

## Guidelines

**DO**:
- Create minimal, focused samples
- Test one feature at a time
- Document findings thoroughly
- Include README explaining the test
- Use realistic test data

**DON'T**:
- Mix multiple features in one sample
- Include production code in sandbox
- Add unnecessary dependencies
- Commit secrets or credentials
- Skip documentation
