# three-stars

Deploy AI-powered web applications to AWS with a single command.

**three-stars** handles three layers of your deployment — the "three stars":

1. **AI Backend** — Amazon Bedrock AgentCore hosts your agent code
2. **Frontend CDN** — Amazon CloudFront + S3 serves your static site
3. **API Edge** — Lambda@Edge routes `/api/*` requests to your agent with SigV4 signing

## Quick Start

### Install

```bash
pip install three-stars
```

This installs the `sss` command (short for **s**imple **s**erverless **s**tack).

### Create a project

```bash
sss init my-app
cd my-app
```

This creates:

```
my-app/
├── three-stars.yml     # Configuration
├── app/                # Frontend (HTML/CSS/JS)
│   └── index.html
└── agent/              # AI agent (Python)
    ├── agent.py        # Strands Agent with SSE streaming
    ├── tools.py        # MCP tool loader
    └── memory.py       # AgentCore Memory session manager
```

### Deploy

```bash
sss deploy
```

This provisions all AWS resources and prints your CloudFront URL. First deploy typically completes in **~5 minutes**:

```
[1/5] S3 storage ready                   0:00:01
[2/5] AgentCore ready                    0:00:48
[3/5] Lambda@Edge function ready         0:00:04
[4/5] CloudFront distribution deployed   0:00:45
[5/5] AgentCore resource policy set      0:00:02

     Post-Deployment Health Check
┌────────────┬───────────────────┬──────────┐
│ Resource   │ ID / Name         │ Status   │
├────────────┼───────────────────┼──────────┤
│ S3 Bucket  │ sss-my-app-…      │ Active   │
│ AgentCore  │ rt-abc123         │ Ready    │
│ CloudFront │ E1234567890       │ Deployed │
└────────────┴───────────────────┴──────────┘

Deployed successfully!
URL: https://d1234567890.cloudfront.net
```

The five steps map to the three stars: **AI Backend** (steps 2, 5), **Frontend CDN** (steps 1, 4), and **API Edge** (step 3). Use `--verbose` for extra detail (ARNs, policy names).

Subsequent deploys are even faster — dependencies are cached and only changed resources update:

```bash
sss deploy  # ~23 seconds on redeploy
```

### Check status

```bash
sss status
```

Use `--sync` to discover actual resources from AWS and update the local state file:

```bash
sss status --sync
```

### Tear down

```bash
sss destroy
```

Use `--name` to discover and destroy resources by project name when the state file is missing:

```bash
sss destroy --name my-app --region us-east-1
```

## Configuration

`three-stars.yml` controls your deployment:

```yaml
name: my-ai-app
region: us-east-1

agent:
  source: ./agent
  model: us.anthropic.claude-sonnet-4-6
  description: "My AI assistant"
  memory: 512

app:
  source: ./app
  index: index.html

api:
  prefix: /api
```

### CLI Options

**`sss deploy`**

| Flag | Description |
|------|------------|
| `--region` | Override AWS region |
| `--profile` | AWS CLI profile name |
| `--yes` / `-y` | Skip confirmation prompts |
| `--force` | Recreate all resources from scratch |
| `--verbose` / `-v` | Print detailed progress |

**`sss status`**

| Flag | Description |
|------|------------|
| `--region` | Override AWS region |
| `--profile` | AWS CLI profile name |
| `--sync` | Refresh state from AWS before showing status |

**`sss destroy`**

| Flag | Description |
|------|------------|
| `--region` | Override AWS region |
| `--profile` | AWS CLI profile name |
| `--yes` / `-y` | Skip confirmation prompt |
| `--name` | Project name for discovery (when state file is missing) |
| `--verbose` / `-v` | Print detailed progress |

## Architecture

```
User Browser
    │
    ▼
CloudFront Distribution (HTTPS CDN)
    ├── /* ──────────► S3 Bucket (static frontend)
    └── /api/* ──────► Lambda@Edge ──► Bedrock AgentCore
                        (SigV4 signing)   (AI agent runtime)
```

### AWS Resources Created

| Resource | Service | Purpose |
|----------|---------|---------|
| S3 Bucket | Amazon S3 | Frontend static files (private, OAC access) |
| AgentCore Runtime | Bedrock AgentCore | Runs AI agent code with Bedrock model access |
| Lambda@Edge Function | AWS Lambda@Edge | SigV4 signing for API requests to AgentCore |
| CloudFront Distribution | Amazon CloudFront | CDN with HTTPS |
| IAM Roles | AWS IAM | Execution permissions (AgentCore, Lambda@Edge) |

## Deployment Performance

Measured from end-to-end developer experience tests:

| Operation | Time | Notes |
|-----------|------|-------|
| Install (`pip install three-stars`) | ~2-6 sec | |
| Init (`sss init my-app`) | <1 sec | Project scaffold |
| First deploy (`sss deploy`) | ~5 min | All AWS resources created |
| Redeploy (`sss deploy`) | **~23 sec** | Cached deps, incremental update |
| Status (`sss status`) | <1 sec | |
| Destroy (`sss destroy`) | ~2 min | Full resource cleanup |

The **~23 second redeploy** is the key iteration metric — change your agent code or frontend, run `sss deploy`, and see results in under 30 seconds.

## Agent Features

The starter agent template includes:

### Strands Agent with SSE Streaming

The agent uses [Strands Agents](https://github.com/strands-agents/strands-agents-python) with Amazon Bedrock and streams responses as Server-Sent Events. The frontend displays token-by-token output in real time, renders responses as Markdown (headings, code blocks, lists, tables), and shows tool call progress indicators when the agent uses MCP tools.

### MCP Tool Support

Agents can use [MCP](https://modelcontextprotocol.io/) tools by placing an `mcp.json` file in the `agent/` directory:

```json
{
  "mcpServers": {
    "my-server": {
      "command": "uvx",
      "args": ["my-mcp-server"]
    }
  }
}
```

Both transport types are supported:

- **stdio** — spawns a subprocess (`command` + `args`)
- **HTTP** — connects to a remote URL (`url` + optional `headers`)

Environment variable references (`${VAR}`) are resolved automatically. AWS credentials from the runtime are forwarded to stdio subprocesses.

### Conversation Memory

When AgentCore Memory is configured, conversation history is preserved across turns within a session. The frontend sends a `session_id` with each request so the agent remembers prior context.

## MCP Server

three-stars includes an [MCP](https://modelcontextprotocol.io/) server so AI agents (Claude Desktop, Claude Code, etc.) can deploy and manage apps programmatically.

### Claude Code

Add to your Claude Code MCP settings:

```json
{
  "mcpServers": {
    "three-stars": {
      "command": "uvx",
      "args": ["three-stars-mcp"]
    }
  }
}
```

For local development:

```json
{
  "mcpServers": {
    "three-stars": {
      "command": "uv",
      "args": ["--directory", "/path/to/three-stars", "run", "three-stars-mcp"]
    }
  }
}
```

### Available Tools

| Tool | Description |
|------|-------------|
| `sss_init` | Create a new three-stars project with config, frontend, and agent templates |
| `sss_deploy` | Deploy the project to AWS (S3, AgentCore, Lambda@Edge, CloudFront) |
| `sss_status` | Show deployment status of AWS resources |
| `sss_destroy` | Destroy all deployed AWS resources |

## Prerequisites

- Python 3.12+
- AWS credentials configured (`aws configure`)
- Permissions for S3, CloudFront, IAM, Lambda, and Bedrock AgentCore

## Development

```bash
# Install in development mode
uv sync

# Run tests
uv run pytest

# Lint
uv run ruff check three_stars/ tests/

# Format
uv run ruff format three_stars/ tests/
```

## License

[Apache License 2.0](LICENSE)
