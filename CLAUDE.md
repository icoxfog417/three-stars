# Claude Agent Workflow Guidelines

This document outlines the workflow and rules for AI-assisted development on this project.

## Project Overview

three-stars is a CLI tool (`sss`) that deploys AI-powered web applications to AWS with a single command. It provisions Bedrock AgentCore, CloudFront + S3, and Lambda@Edge resources. Primary language is Python (>= 3.12). Lint with `ruff`, format with `ruff format`.

## General Principles

1. **Implement quickly, don't over-plan.** When asked to implement something, start writing code. Do NOT spend entire sessions on exploration and planning without producing implementation code. If research is needed, timebox it to a few minutes and confirm the approach before deep-diving.
2. **Prefer simple, minimal solutions.** Do NOT introduce unnecessary abstractions, helper functions, or new modules unless explicitly asked. When refactoring, keep the architecture flat and straightforward. If you think a new abstraction is needed, ask first.
3. **Verify AWS facts before stating them.** When unsure about AWS service capabilities or API support, check official AWS documentation FIRST before making assumptions. Never state something is unsupported without verifying.

## Specification Management

### Directory Structure

All project specifications are maintained in the `spec/` directory:

```
spec/
├── requirements.md          # User experience and necessary features
├── design.md                # Architecture and component design
├── tasks.md                 # Implementation task list (sprint tracking)
├── implementation_qa.md     # Technical Q&A from sandbox verification
└── proposals/               # Design proposal documents
    └── yyyyMMdd_{proposal_name}.md
```

### Specification Files

#### requirements.md
- Defines user experience flows
- Lists all necessary features
- Describes user personas and use cases
- Outlines functional and non-functional requirements

#### design.md
- Documents system architecture
- Describes component design and interactions
- Defines data models and schemas
- Specifies API contracts and interfaces
- Includes technical decisions and rationale

#### tasks.md
- Maintains implementation task list
- Tracks progress on features and bug fixes
- Organizes tasks by sprint, priority, and dependencies
- Records completion status

#### implementation_qa.md
- Documents technical questions and verified answers
- Links to sandbox experiments that verified each answer
- Serves as a project-specific knowledge base
- Prevents re-investigation of solved problems

### Change Management Process

**IMPORTANT**: When making changes to `requirements.md`, `design.md`, or `tasks.md`, you MUST follow this process:

1. **Create a Proposal Document**
   - Location: `spec/proposals/`
   - Naming convention: `yyyyMMdd_{your_proposal_name}.md`
   - Example: `20260201_add_batch_upload_feature.md`

2. **Proposal Document Structure**
   ```markdown
   # Proposal: [Title]

   **Date**: YYYY-MM-DD
   **Author**: [Name or "Claude Agent"]
   **Status**: [Proposed/Approved/Implemented/Rejected]

   ## Background
   [Why this change is needed]

   ## Proposal
   [Detailed description of the proposed changes]

   ## Impact
   - Requirements: [What changes in requirements.md]
   - Design: [What changes in design.md]
   - Tasks: [What new tasks will be added to tasks.md]

   ## Alternatives Considered
   [Other approaches and why they were not chosen]

   ## Implementation Plan
   [Step-by-step plan if approved]
   ```

3. **Update Specification Files**
   - After creating the proposal, update the relevant spec files
   - Reference the proposal document in commit messages
   - Ensure consistency across all three spec files

### Example Workflow

```bash
# 1. Create proposal
# File: spec/proposals/20260201_implement_feature_x.md

# 2. Update requirements.md
# Add: New feature requirements

# 3. Update design.md
# Add: Architecture changes for the feature

# 4. Update tasks.md
# Add: Implementation tasks

# 5. Commit with reference
git commit -m "feat: Add feature X proposal (see spec/proposals/20260201_implement_feature_x.md)"
```

## Development Guidelines

### Before Starting Work

1. Review `spec/requirements.md` to understand user needs
2. Check `spec/design.md` for architectural constraints
3. Consult `spec/tasks.md` for current priorities
4. Check existing proposals in `spec/proposals/` for context
5. Review `spec/implementation_qa.md` for verified technical patterns

### Making Changes

1. For new features or significant changes:
   - Create a proposal document first
   - Update all relevant spec files
   - Ensure consistency across documentation

2. For bug fixes or minor improvements:
   - Update `tasks.md` to track the work
   - Update `design.md` if implementation details change
   - Create a proposal only if the fix requires design changes

3. For documentation updates:
   - Keep README.md in sync with spec files
   - Update inline code documentation as needed

### Testing and Validation

- After making changes, always run `uv run pytest` and `uv run ruff check three_stars/ tests/` before committing. Verify all tests pass.
- Ensure changes align with requirements in `spec/requirements.md`
- Validate against architecture in `spec/design.md`
- Update task status in `spec/tasks.md`

### Commit Messages

- Reference proposal documents when applicable
- Use conventional commit format: `feat:`, `fix:`, `docs:`, `refactor:`, etc.
- Include context and rationale

Example:
```
feat: Add user authentication flow

Implements OAuth-based authentication.
See spec/proposals/20260201_implement_auth.md for details.

Closes #123
```

## Sandbox Verification Workflow

### Purpose

Before implementing production features, verify technical approaches and integrations in isolated sandbox environments. This reduces risk and documents working patterns.

### Directory Structure

```
.sandbox/
├── README.md                     # Sandbox overview and guidelines
├── .gitkeep                      # Preserve directory in git
├── {feature-name}/              # Individual verification tests
│   ├── README.md                # What this test verifies
│   ├── src/                     # Source code
│   └── package.json             # Dependencies (if applicable)
└── ...
```

**Note**: The `.sandbox/` directory is in `.gitignore` except for `.gitkeep` and `README.md`. Sandbox samples are disposable learning environments.

### Workflow Process

**IMPORTANT**: Follow the "Question First" approach - always start by formulating clear questions before creating sandbox samples.

#### Step 1: Question First
Before implementing any feature, identify and document technical questions:

```markdown
Example questions:
- How to configure authentication with provider X?
- How to implement file upload with progress tracking?
- How to set up real-time data synchronization?
```

**Add questions to `spec/implementation_qa.md` FIRST**, with status "⏳ Pending Verification"

#### Step 2: Sandbox Examination
Create minimal sandbox sample to answer the question:

```bash
# Example: For testing authentication
mkdir -p .sandbox/auth-test
cd .sandbox/auth-test

# Create minimal test implementation
# ... configure and test ...
```

**Update question status to "🔬 In Progress"** in `spec/implementation_qa.md`

#### Step 3: Test and Iterate
- Build minimal reproducible sample
- Test functionality thoroughly
- Debug and fix issues
- Verify it works as expected
- Document unexpected behaviors

#### Step 4: Write Answer
Document findings in `spec/implementation_qa.md`:

- Update the question entry with detailed answer
- Include code samples from sandbox
- Document key findings and gotchas
- Add references to official documentation
- **Update status to "✅ Verified"**

#### Step 5: Reference in Production
When implementing production features:

- Check `spec/implementation_qa.md` for related Q&A entries
- Follow verified patterns from sandbox
- Adapt patterns to production requirements
- If new questions arise, return to Step 1

### Q&A Documentation Format

Each entry in `spec/implementation_qa.md` should include:

```markdown
### Q#: How to [specific question]?

**Status**: ✅ Verified / ⏳ Pending / 🔬 In Progress

**Answer**: [Detailed explanation with context]

**Code Sample**:
\```
// Minimal working example
\```

**Verified in**: `.sandbox/{directory}/`

**Key Findings**:
- Important discovery 1
- Important discovery 2

**Gotchas**:
- Potential issue to watch for
- Common mistake to avoid

**References**:
- [Official Documentation]
- [Related Q&A]
```

### Sandbox Guidelines

**DO**:
- Create minimal, focused samples
- Test one feature at a time
- Document findings thoroughly
- Include README explaining the test
- Use realistic test data

**DON'T**:
- Mix multiple features in one sample
- Include production code
- Add unnecessary dependencies
- Commit secrets or credentials
- Skip documentation
- Leave samples in broken state

### Integration with Development Process

**Before Starting Feature Development**:
1. **Ask Questions First**: What technical unknowns exist?
2. **Check Existing Q&A**: Review `spec/implementation_qa.md` for answers
3. **Document New Questions**: Add unanswered questions to Q&A (status: ⏳ Pending)
4. **Verify Before Building**: Create sandbox samples to answer questions
5. **Document Answers**: Update Q&A with findings (status: ✅ Verified)
6. **Then Implement**: Build production code with confidence

**During Feature Development**:
- Reference verified patterns from Q&A
- Adapt patterns to production requirements
- If new questions arise, document them first
- Don't guess - verify in sandbox if uncertain

**After Feature Completion**:
- Update Q&A if new findings discovered during implementation
- Document any deviations from sandbox patterns
- Keep sandbox samples updated with latest best practices

## Code Quality Standards

### Principles

We maintain high code quality through automated tooling and consistent practices:

1. **Zero Warnings Policy**: All code must pass linting with zero warnings
2. **Consistent Formatting**: All code is auto-formatted using the project formatter
3. **Type Safety**: Use strict type checking where available, avoid `any` types without justification
4. **Automated Enforcement**: Pre-commit hooks ensure quality before code is committed
5. **Minimal Dependencies**: Only include dependencies that are truly necessary

### Tooling

- **Python >= 3.12** — `from __future__ import annotations` is not needed in new code
- **Linter/Formatter**: `ruff` — run `uv run ruff check three_stars/ tests/` and `uv run ruff format three_stars/ tests/`
- **Tests**: `uv run pytest`
- **Package manager**: `uv`

### Best Practices

1. **Write Clean Code First**: Don't rely solely on auto-fix
2. **Review Linter Warnings**: Understand why they appear
3. **Document Complex Logic**: Add comments for non-obvious code
4. **Keep Functions Small**: Single responsibility principle
5. **Use Descriptive Names**: Variables and functions should be self-documenting
6. **Avoid Premature Optimization**: Make it work, then make it fast
7. **Test Edge Cases**: Don't just test the happy path

### Code Review Checklist

Before requesting review, ensure:
- All linting passes with zero warnings
- Code is properly formatted
- Types are explicit and correct
- No debug statements in production code (e.g., `console.log`, `print`)
- Comments explain "why", not "what"
- Tests cover new functionality
- No hardcoded secrets or credentials
- Error handling is appropriate

### Security Considerations

- Never commit secrets or API keys
- Use environment variables for configuration
- Sanitize user inputs
- Validate data at system boundaries
- Keep dependencies up to date
- Run security audits regularly

## Code Review Guidelines

### Responding to Automated Reviews (Copilot, etc.)

When addressing automated PR review comments, apply critical judgment rather than accepting all suggestions:

1. **Evaluate each comment independently** - automated reviewers often suggest over-engineered solutions
2. **Reply to every comment** with a clear disposition: fixed, rejected (with rationale), or addressed with comment-only
3. **Post a summary review** before inline replies so the PR author has an overview

### Common Patterns to Watch For

**Accept and fix:**
- Dead code paths (e.g., null checks after functions that raise on failure)
- Inconsistent use of shared utilities (if a helper exists, use it everywhere)
- Unnecessary dynamic imports when the module is already partially imported

**Reject with rationale:**
- Expanding utility interfaces for hypothetical future callers (YAGNI)
- Adding streaming/virtual-scrolling for human-scale datasets (premature optimization)
- Parallelizing sequential batches that exist specifically for rate limiting

**Address with code comment only:**
- Missing pagination on queries that return tiny result sets (document the assumption instead)

## Documentation

- For Japanese translations (README_ja.md), produce natural-sounding Japanese rather than literal translations. Sync Japanese files whenever English READMEs are updated.
- Keep README.md in sync with actual CLI behavior and deployment steps.

## Communication

- Use clear, descriptive language in all documentation
- Explain technical decisions in design.md
- Keep proposals focused and actionable
- Update documentation as the project evolves

## Questions and Issues

If you encounter ambiguity or need clarification:
1. Check existing proposals for similar discussions
2. Review requirements.md and design.md for context
3. Create a proposal to document the question and proposed resolution
4. Seek human input for critical architectural decisions

## Version Control

- All spec files are version-controlled
- Proposals serve as historical record of decisions
- Tag major milestones for easy reference
- Keep documentation in sync with implementation

---

**Last Updated**: 2026-02-22
**Maintained By**: Project Team
