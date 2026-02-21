# three-stars

Deploy AI-powered web applications to AWS with a single command.

**three-stars** handles three layers of your deployment — the "three stars":

1. **AI Backend** — Amazon Bedrock AgentCore hosts your agent code
2. **Frontend CDN** — Amazon CloudFront + S3 serves your static site
3. **API Bridge** — Lambda routes `/api/*` requests to your agent

## Quick Start

### Install

```bash
pip install three-stars
```

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
python agent/agent.py
```

### Deploy

```bash
sss deploy
```

This provisions all AWS resources and prints your CloudFront URL:

```
[1/5] S3 storage ready
[2/5] AgentCore ready
[3/5] Lambda API bridge ready
[4/5] Lambda@Edge function ready
[5/5] CloudFront distribution created (propagation ~5-10 min)

Deployed successfully!
URL: https://d1234567890.cloudfront.net
```

### Check status

```bash
sss status
```

### Tear down

```bash
sss destroy
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
| `--force` | Recreate all resources from scratch |
| `--verbose` / `-v` | Print detailed progress |

## Architecture

```
User Browser
    │
    ▼
CloudFront Distribution (HTTPS CDN)
    ├── /* ──────────► S3 Bucket (static frontend)
    └── /api/* ──────► Lambda API Bridge ──► Bedrock AgentCore
                        (via Lambda@Edge      (AI agent runtime)
                         OAC signing)
```

### AWS Resources Created

| Resource | Service | Purpose |
|----------|---------|---------|
| S3 Bucket | Amazon S3 | Frontend static files (private, OAC access) |
| AgentCore Runtime | Bedrock AgentCore | Runs AI agent code with Bedrock model access |
| Lambda Function | AWS Lambda | Bridges API requests to AgentCore |
| Lambda@Edge Function | AWS Lambda@Edge | Computes SHA256 for OAC request signing |
| CloudFront Distribution | Amazon CloudFront | CDN with HTTPS |
| IAM Roles | AWS IAM | Execution permissions (AgentCore, Lambda, Lambda@Edge) |

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
