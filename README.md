# three-stars

Deploy AI-powered web applications to AWS with a single command.

**three-stars** provisions three AWS resources — the "three stars" of your deployment:

1. **Amazon Bedrock AgentCore** — Hosts your AI agent backend
2. **Amazon CloudFront + S3** — Serves your static frontend via CDN
3. **CloudFront Functions** — Routes `/api/*` requests to the agent

## Quick Start

### Install

```bash
pip install three-stars
```

### Create a project

```bash
three-stars init my-app
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

### Deploy

```bash
three-stars deploy
```

This provisions all AWS resources and prints your CloudFront URL:

```
✓ IAM role ready
✓ S3 bucket ready
✓ Uploaded 2 files
✓ Agent packaged
✓ AgentCore runtime active
✓ CloudFront Function ready
✓ CloudFront distribution created

Deployed successfully!
URL: https://d1234567890.cloudfront.net
```

### Check status

```bash
three-stars status
```

### Tear down

```bash
three-stars destroy
```

## Configuration

`three-stars.yml` controls your deployment:

```yaml
name: my-ai-app
region: us-east-1

agent:
  source: ./agent
  model: anthropic.claude-sonnet-4-20250514
  description: "My AI assistant"
  memory: 512

app:
  source: ./app
  index: index.html

api:
  prefix: /api
```

### CLI Options

| Flag | Description |
|------|------------|
| `--region` | Override AWS region |
| `--profile` | AWS CLI profile name |
| `--yes` / `-y` | Skip confirmation prompts |

## Architecture

```
User Browser
    │
    ▼
CloudFront Distribution (HTTPS CDN)
    ├── /* ──────────► S3 Bucket (static frontend)
    └── /api/* ──────► CloudFront Function ──► Bedrock AgentCore
                        (URL router)            (AI agent runtime)
```

### AWS Resources Created

| Resource | Service | Purpose |
|----------|---------|---------|
| S3 Bucket | Amazon S3 | Frontend static files (private, OAC access) |
| AgentCore Runtime | Bedrock AgentCore | Runs AI agent code with Bedrock model access |
| CloudFront Distribution | Amazon CloudFront | CDN with HTTPS |
| CloudFront Function | CloudFront Functions | Routes API requests to AgentCore |
| IAM Role | AWS IAM | AgentCore execution permissions |

## Prerequisites

- Python 3.11+
- AWS credentials configured (`aws configure`)
- Permissions for S3, CloudFront, IAM, and Bedrock AgentCore

## Development

```bash
# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/
```

## License

[Apache License 2.0](LICENSE)
