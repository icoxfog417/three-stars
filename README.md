# Agentic Coding Template

A GitHub template repository for spec-driven, AI-assisted software development. This template provides a structured workflow for working with AI coding agents (Claude Code, Kiro) with built-in guardrails for quality and consistency.

## What This Template Provides

### Specification-Driven Development
All project decisions flow through a structured `spec/` directory:
- **requirements.md** - What to build (user personas, features, acceptance criteria)
- **design.md** - How to build it (architecture, data models, API contracts)
- **tasks.md** - Sprint-based task tracking with status markers
- **implementation_qa.md** - Technical Q&A knowledge base from sandbox experiments
- **proposals/** - Change proposals required before modifying spec files

### Change Management via Proposals
Before changing any spec file, a proposal document must be created in `spec/proposals/`. This ensures:
- Changes are deliberate, not accidental
- Alternatives are considered before implementation
- A historical record of decisions exists
- Multiple agents/developers stay coordinated

### Sandbox Verification Workflow
The `.sandbox/` directory provides isolated experimentation space:
1. Document technical questions in `spec/implementation_qa.md`
2. Create minimal sandbox experiments to verify answers
3. Document findings before implementing in production
4. Build a project-specific knowledge base over time

### Multi-Agent Support

| Tool | Configuration | Purpose |
|------|--------------|---------|
| **Claude Code** | `CLAUDE.md` + `.claude/skills/` | Primary workflow guidelines, project rules, and reusable skills |
| **Kiro** | `.kiro/agents/` | Specialized agents (developer, investigator, reviewer) |
| **VS Code** | `.vscode/settings.json` | Editor configuration |

## Getting Started

### 1. Create Repository from Template

Click "Use this template" on GitHub, or:

```bash
gh repo create my-project --template icoxfog417/agentic-coding-template
cd my-project
```

### 2. Customize CLAUDE.md

Edit `CLAUDE.md` to add:
- Your project description in the Project Overview section
- Project-specific code quality tooling (linter, formatter, pre-commit hooks)
- Any additional guidelines specific to your tech stack

### 3. Fill In Spec Files

Start with `spec/requirements.md`:
- Define your user personas
- List functional requirements with IDs (e.g., `REQ-XX-001`)
- Add non-functional requirements and user stories

Then `spec/design.md`:
- Document your architecture (Mermaid diagrams work well)
- Define your technology stack
- Describe data models and API contracts

Then `spec/tasks.md`:
- Organize work into sprints
- Break features into small, testable tasks

### 4. Configure Your Tools

**For Claude Code users**: `CLAUDE.md` is automatically read. Skills in `.claude/skills/` provide slash commands (`/propose`, `/sprint`, `/kickoff`, `/investigate`). Add project-specific skills for your framework patterns.

**For Kiro users**: Update `.kiro/agents/` with project-specific MCP servers and resource paths.

### 5. Start Development

```bash
# Create your first proposal (or use /propose in Claude Code)
touch spec/proposals/$(date +%Y%m%d)_initial_setup.md

# Start sandbox verification for technical unknowns
mkdir -p .sandbox/my-first-test

# Begin sprint work
# Update spec/tasks.md as you progress
```

## Directory Structure

```
.
├── CLAUDE.md                        # AI agent workflow guidelines (primary)
├── README.md                        # This file
├── LICENSE                          # Project license
├── .gitignore                       # Git ignore rules
├── .claude/
│   └── skills/                      # Claude Code reusable skills
│       ├── propose/                 # /propose - Create change proposals
│       ├── sprint/                  # /sprint - Sprint analysis & parallel work
│       ├── kickoff/                 # /kickoff - Work unit onboarding
│       └── investigate/             # /investigate - Sandbox verification
├── .kiro/
│   └── agents/                      # Kiro specialized agents
│       ├── developer.json           # Feature implementation agent
│       ├── investigator.json        # Sandbox experimentation agent
│       └── reviewer.json            # Code review enforcement agent
├── .vscode/
│   └── settings.json                # VS Code editor settings
├── spec/
│   ├── requirements.md              # User experience and features
│   ├── design.md                    # Architecture and component design
│   ├── tasks.md                     # Sprint-based task tracking
│   ├── implementation_qa.md         # Technical Q&A knowledge base
│   └── proposals/                   # Change proposal documents
│       └── .gitkeep
└── .sandbox/
    ├── README.md                    # Sandbox guidelines
    └── .gitkeep                     # Preserve directory in git
```

## Claude Code Skills

The template includes four built-in skills for Claude Code:

| Skill | Command | Purpose |
|-------|---------|---------|
| **propose** | `/propose add dark mode` | Creates a formatted proposal document and updates spec files |
| **sprint** | `/sprint` or `/sprint 3` | Analyzes sprint status, maps dependencies, identifies parallel work |
| **kickoff** | `/kickoff Unit A` | Generates onboarding briefing with context, tasks, and acceptance criteria |
| **investigate** | `/investigate How to configure OAuth?` | Runs sandbox verification workflow in isolated subagent |

### Adding Project-Specific Skills

Create `.claude/skills/{skill-name}/SKILL.md`:

```markdown
---
name: skill-name
description: When Claude should use this skill
argument-hint: [expected arguments]
---

# Instructions

Your project-specific patterns, templates, and conventions here.
```

Skills support advanced features:
- `context: fork` - Run in isolated subagent
- `allowed-tools: Read, Grep` - Restrict tool access
- `!`backtick commands`` - Inject dynamic context (e.g., `!git status`)
- Supporting files in the skill directory (templates, examples, scripts)

See [Claude Code skills documentation](https://docs.anthropic.com/en/docs/claude-code/skills) for details.

## Workflow Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     Development Cycle                        │
│                                                             │
│  1. Review specs (requirements.md, design.md, tasks.md)     │
│              ↓                                              │
│  2. /propose → Create proposal in spec/proposals/           │
│              ↓                                              │
│  3. /investigate → Verify unknowns in .sandbox/             │
│              ↓                                              │
│  4. /kickoff → Onboard to work unit with full context       │
│              ↓                                              │
│  5. Implement with confidence using verified patterns       │
│              ↓                                              │
│  6. /sprint → Track progress, coordinate parallel work      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Customization Guide

### Adding MCP Servers

For Claude Code, create `.claude/mcp.json`:
```json
{
  "mcpServers": {
    "server-name": {
      "command": "command",
      "args": ["args"]
    }
  }
}
```

For Kiro, add to the `mcpServers` field in `.kiro/agents/*.json`.

### Extending the Reviewer Agent

Edit `.kiro/agents/reviewer.json` to add project-specific review rules:
- Add allowed lint/test commands to `toolsSettings.execute_bash.allowedCommands`
- Add resource paths for files the reviewer should monitor

## Contributing

1. Check `spec/tasks.md` for current priorities
2. Create a proposal in `spec/proposals/` for significant changes
3. Follow the guidelines in `CLAUDE.md`
4. Use sandbox verification for technical unknowns

## License

[Apache License 2.0](LICENSE)
