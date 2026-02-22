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
    ├── agent.py
    └── requirements.txt
```

### Test locally

```bash
python agent/agent.py "What is Amazon Bedrock?"
```

This calls your agent handler directly. Requires AWS credentials (`aws configure`) since the starter agent invokes a Bedrock model.

### Deploy

```bash
sss deploy
```

This provisions all AWS resources and prints your CloudFront URL:

```
[1/5] S3 storage ready                   0:00:02
[2/5] AgentCore ready                    0:00:15
[3/5] Lambda@Edge function ready         0:00:04
[4/5] CloudFront distribution deployed   0:05:32
[5/5] AgentCore resource policy set      0:00:01

     Post-Deployment Health Check
┌────────────┬───────────────────┬──────────┐
│ Resource   │ ID / Name         │ Status   │
├────────────┼───────────────────┼──────────┤
│ S3 Bucket  │ sss-my-app-…      │ Active   │
│ AgentCore  │ rt-abc123         │ Active   │
│ CloudFront │ E1234567890       │ Deployed │
└────────────┴───────────────────┴──────────┘

Deployed successfully!
URL: https://d1234567890.cloudfront.net
```

The five steps map to the three stars: **AI Backend** (steps 2, 5), **Frontend CDN** (steps 1, 4), and **API Edge** (step 3). Use `--verbose` for extra detail (ARNs, policy names).

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
| `sss_init` | Create a new three-stars project |
| `sss_deploy` | Deploy the project to AWS |
| `sss_status` | Show deployment status |
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
