# Implementation Plan: three-stars AWS CLI Tool

**Date**: 2026-02-21
**Status**: Proposed

## 1. Project Overview

**three-stars** is a CLI tool that deploys AI-powered web applications to AWS with a single command. It provisions three core AWS resources (hence "three stars"):

1. **Amazon Bedrock AgentCore** — Hosts the AI agent (backend logic + model invocation)
2. **Amazon CloudFront** — CDN serving the static frontend (React/Vue/vanilla)
3. **Amazon CloudFront Functions** — Routes API requests from frontend to AgentCore endpoint

The user experience: `three-stars deploy` takes a project directory containing a frontend (`app/`) and agent code (`agent/`), uploads them, and returns a working URL.

## 2. Architecture

### 2.1 High-Level Flow

```
User runs: three-stars deploy ./my-project
                    │
                    ▼
        ┌──────────────────────┐
        │  CLI (Click + Rich)  │
        │  Parse config, validate│
        └──────────┬───────────┘
                   │
    ┌──────────────┼──────────────┐
    │              │              │
    ▼              ▼              ▼
┌────────┐  ┌──────────┐  ┌──────────────┐
│ Agent  │  │ Frontend │  │  CloudFront  │
│ Deploy │  │  Upload  │  │  Functions   │
│(AgentCore)│ │ (S3+CDN) │  │  (Router)   │
└────┬───┘  └────┬─────┘  └──────┬───────┘
     │           │               │
     ▼           ▼               ▼
  AgentCore   S3 Bucket +    CF Function
  Runtime     CloudFront     associates to
  (running)   Distribution   distribution
                    │
                    ▼
              https://d1234.cloudfront.net
              ├── /           → index.html (S3)
              ├── /assets/*   → static files (S3)
              └── /api/*      → AgentCore endpoint
```

### 2.2 AWS Resources Created

| Resource | AWS Service | Purpose |
|----------|------------|---------|
| Agent Runtime | Bedrock AgentCore | Runs AI agent code with model access |
| S3 Bucket | Amazon S3 | Stores frontend static files |
| CloudFront Distribution | Amazon CloudFront | CDN with HTTPS, serves frontend |
| CloudFront Function | CloudFront Functions | Routes `/api/*` to AgentCore |
| IAM Role | IAM | AgentCore execution role |

### 2.3 Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Language | Python 3.11+ | AWS SDK (boto3) is Python-native; AgentCore SDK is Python |
| CLI Framework | Click | Mature, composable, no heavy deps (vs Typer needing FastAPI ecosystem) |
| Output/UX | Rich | Beautiful terminal output, progress bars, tables |
| AWS SDK | boto3 | Official AWS SDK, required for all resource operations |
| Config Format | YAML | Human-readable, standard for AWS configs |
| Packaging | pyproject.toml + hatchling | Modern Python packaging standard |
| Testing | pytest + moto | moto mocks AWS services for offline testing |

## 3. Project Structure

```
three-stars/
├── pyproject.toml              # Package config, dependencies, entry points
├── src/
│   └── three_stars/
│       ├── __init__.py         # Package version
│       ├── cli.py              # Click CLI entry point (deploy, destroy, status, init)
│       ├── config.py           # YAML config loading + validation
│       ├── deploy.py           # Orchestrates full deployment
│       ├── destroy.py          # Tears down all resources
│       ├── status.py           # Shows deployment status
│       └── aws/
│           ├── __init__.py
│           ├── session.py      # boto3 session + region handling
│           ├── agentcore.py    # AgentCore runtime CRUD (adapted from toolkit patterns)
│           ├── s3.py           # S3 bucket + static file upload
│           ├── cloudfront.py   # CloudFront distribution CRUD
│           └── cf_function.py  # CloudFront Functions (API router)
├── tests/
│   ├── conftest.py             # Shared fixtures (moto mocks, sample configs)
│   ├── test_cli.py             # CLI integration tests
│   ├── test_config.py          # Config parsing tests
│   ├── test_deploy.py          # Deploy orchestration tests
│   └── aws/
│       ├── test_agentcore.py
│       ├── test_s3.py
│       ├── test_cloudfront.py
│       └── test_cf_function.py
├── templates/
│   └── starter/                # Project template for `three-stars init`
│       ├── three-stars.yml
│       ├── app/
│       │   └── index.html      # Minimal frontend
│       └── agent/
│           ├── requirements.txt
│           └── agent.py        # Starter agent code
├── spec/                       # (existing) specification documents
├── CLAUDE.md                   # (existing) agent guidelines
└── README.md                   # Updated with usage docs
```

## 4. CLI Commands

### 4.1 `three-stars init [--template starter]`

Scaffolds a new project directory with the required structure:

```
my-project/
├── three-stars.yml     # Configuration
├── app/                # Frontend static files
│   └── index.html
└── agent/              # Agent code
    ├── requirements.txt
    └── agent.py
```

### 4.2 `three-stars deploy [PROJECT_DIR]`

Deploys the project. Steps:

1. Load and validate `three-stars.yml`
2. Package agent code (zip the `agent/` directory)
3. Create/update S3 bucket and upload `app/` contents
4. Create/update AgentCore runtime with agent zip
5. Create/update CloudFront Function (API router)
6. Create/update CloudFront Distribution with S3 origin + function association
7. Save deployment state to `.three-stars-state.json` (in project dir)
8. Print the CloudFront URL

Flags:
- `--region` — Override AWS region (default: from config or `us-east-1`)
- `--profile` — AWS CLI profile name
- `--yes` — Skip confirmation prompt

### 4.3 `three-stars status [PROJECT_DIR]`

Shows current deployment status by reading `.three-stars-state.json` and querying AWS.

### 4.4 `three-stars destroy [PROJECT_DIR]`

Tears down all deployed resources in reverse order. Requires confirmation.

## 5. Configuration File: `three-stars.yml`

```yaml
# Project name (used for resource naming)
name: my-ai-app

# AWS region
region: us-east-1

# Agent configuration
agent:
  # Path to agent source directory
  source: ./agent
  # Bedrock model to use
  model: anthropic.claude-sonnet-4-20250514
  # Agent description
  description: "My AI assistant"
  # Runtime memory (MB)
  memory: 512

# Frontend configuration
app:
  # Path to frontend static files
  source: ./app
  # Index document
  index: index.html
  # Error document (optional)
  error: error.html

# API routing
api:
  # URL path prefix for API requests (routed to agent)
  prefix: /api
```

## 6. Deployment State File: `.three-stars-state.json`

Stored in the project directory. Tracks deployed resource IDs for updates and teardown.

```json
{
  "version": 1,
  "project_name": "my-ai-app",
  "region": "us-east-1",
  "deployed_at": "2026-02-21T12:00:00Z",
  "resources": {
    "s3_bucket": "three-stars-my-ai-app-abc123",
    "agentcore_runtime_id": "rt-xxxx",
    "agentcore_endpoint": "https://agentcore.us-east-1.amazonaws.com/...",
    "cloudfront_distribution_id": "E1234567890",
    "cloudfront_function_name": "three-stars-my-ai-app-router",
    "cloudfront_domain": "d1234567890.cloudfront.net",
    "iam_role_arn": "arn:aws:iam::123456789012:role/three-stars-my-ai-app"
  }
}
```

## 7. AWS Module Details

### 7.1 `aws/agentcore.py` — AgentCore Runtime Management

Adapted from patterns in `bedrock-agentcore-starter-toolkit`. Key operations:

```python
def create_agent_runtime(session, name, agent_zip_path, model_id, role_arn, memory_mb) -> dict:
    """Create a new AgentCore runtime.

    1. Upload agent zip to S3 staging bucket
    2. Call bedrock-agentcore CreateAgentRuntime
    3. Wait for runtime to reach ACTIVE status
    4. Return runtime details including endpoint URL
    """

def update_agent_runtime(session, runtime_id, agent_zip_path) -> dict:
    """Update existing runtime with new agent code."""

def delete_agent_runtime(session, runtime_id) -> None:
    """Delete an AgentCore runtime."""

def get_agent_runtime_status(session, runtime_id) -> dict:
    """Get current runtime status and endpoint."""
```

**Key patterns from toolkit:**
- Zip packaging of agent directory with `requirements.txt`
- S3 upload of zip before creating runtime
- Polling for ACTIVE status with exponential backoff
- IAM role with `bedrock:InvokeModel` + `bedrock-agentcore:*` permissions

### 7.2 `aws/s3.py` — S3 Static Hosting

```python
def create_bucket(session, bucket_name, region) -> str:
    """Create S3 bucket for static files. Returns bucket name."""

def upload_directory(session, bucket_name, local_dir, prefix="") -> int:
    """Upload a directory to S3, returns file count.
    Sets Content-Type based on file extension."""

def delete_bucket(session, bucket_name) -> None:
    """Empty and delete bucket."""
```

### 7.3 `aws/cloudfront.py` — CloudFront Distribution

```python
def create_distribution(session, bucket_name, bucket_region,
                        function_arn, index_doc, api_prefix) -> dict:
    """Create CloudFront distribution with:
    - S3 origin for static files (OAC)
    - CloudFront Function association for viewer-request
    Returns distribution ID and domain name."""

def delete_distribution(session, distribution_id) -> None:
    """Disable and delete distribution."""
```

### 7.4 `aws/cf_function.py` — CloudFront Functions (API Router)

```python
def create_router_function(session, name, agentcore_endpoint, api_prefix) -> str:
    """Create a CloudFront Function that:
    - Matches requests with path starting with api_prefix
    - Rewrites them to the AgentCore endpoint
    - Passes through all other requests to S3 origin
    Returns function ARN."""
```

The CloudFront Function JavaScript code (embedded in Python):

```javascript
function handler(event) {
    var request = event.request;
    var uri = request.uri;

    if (uri.startsWith('/api/')) {
        // Rewrite to AgentCore endpoint
        request.origin = {
            custom: {
                domainName: 'AGENTCORE_ENDPOINT_HOST',
                port: 443,
                protocol: 'https',
                path: '',
                sslProtocols: ['TLSv1.2'],
                readTimeout: 30,
                keepaliveTimeout: 5
            }
        };
        request.uri = uri.replace('/api', '');
        request.headers['host'] = { value: 'AGENTCORE_ENDPOINT_HOST' };
    }

    return request;
}
```

**Note**: CloudFront Functions run at edge, JavaScript only, max 10KB, < 1ms execution. This is simpler and cheaper than Lambda@Edge for pure routing.

### 7.5 `aws/session.py` — boto3 Session Management

```python
def create_session(region=None, profile=None) -> boto3.Session:
    """Create boto3 session with optional profile and region."""

def get_account_id(session) -> str:
    """Get AWS account ID via STS."""
```

## 8. Implementation Sprints

### Sprint 0: Foundation (Tasks 1-6)

**Goal**: Working Python package with CLI skeleton and test infrastructure.

1. Create `pyproject.toml` with dependencies (click, rich, boto3, pyyaml)
2. Create `src/three_stars/__init__.py` and package structure
3. Implement `cli.py` with Click command group (deploy/destroy/status/init stubs)
4. Implement `config.py` — YAML loading, validation, defaults
5. Set up `tests/` with pytest, conftest, config tests
6. Set up linting (ruff) and formatting (ruff format) in pyproject.toml

### Sprint 1: AWS Core Modules (Tasks 7-12)

**Goal**: Individual AWS operations working and tested.

7. Implement `aws/session.py` — boto3 session creation, account ID
8. Implement `aws/s3.py` — bucket CRUD, file upload with MIME types
9. Implement `aws/cloudfront.py` — distribution CRUD with OAC
10. Implement `aws/cf_function.py` — CloudFront Function CRUD with JS template
11. Implement `aws/agentcore.py` — Runtime CRUD (adapted from toolkit patterns)
12. Write unit tests with moto mocks for S3/CloudFront (AgentCore: mock boto3 calls)

### Sprint 2: Orchestration (Tasks 13-17)

**Goal**: Full deploy/destroy/status workflows working end-to-end.

13. Implement `deploy.py` — Orchestrate all AWS modules in sequence with Rich progress
14. Implement `destroy.py` — Reverse teardown with confirmation
15. Implement `status.py` — Query resource states and display table
16. Wire orchestrators into CLI commands
17. Implement state file read/write (`.three-stars-state.json`)

### Sprint 3: Init + Polish (Tasks 18-22)

**Goal**: Complete CLI with init command, error handling, and user experience.

18. Create `templates/starter/` with minimal project template
19. Implement `init` command — Copy template, prompt for project name
20. Add comprehensive error handling (missing credentials, invalid config, etc.)
21. Add `--yes` flag, `--region`/`--profile` overrides
22. Integration tests for CLI commands

### Sprint 4: Documentation + Release (Tasks 23-25)

**Goal**: Ready to publish.

23. Update README.md with installation, quick start, configuration reference
24. Update spec files (requirements.md, design.md, tasks.md)
25. Add GitHub Actions CI workflow

## 9. Dependencies

### Runtime Dependencies

```toml
[project]
dependencies = [
    "click>=8.1",
    "rich>=13.0",
    "boto3>=1.35",
    "pyyaml>=6.0",
]
```

### Development Dependencies

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "moto[s3,cloudfront,iam,sts]>=5.0",
    "ruff>=0.9",
]
```

### Entry Point

```toml
[project.scripts]
three-stars = "three_stars.cli:main"
```

## 10. Key Design Decisions

### Decision 1: Click over Typer
Click has zero transitive deps beyond itself, is well-documented, and doesn't need type annotations for CLI args (keeping code lighter). Typer adds FastAPI/Pydantic ecosystem deps.

### Decision 2: CloudFront Functions over Lambda@Edge
CloudFront Functions are simpler (JavaScript only, <10KB), cheaper (1/6th the cost), faster (sub-millisecond), and sufficient for URL rewriting/routing. Lambda@Edge would only be needed for body transformation or complex auth.

### Decision 3: Reference toolkit patterns, don't depend on it
The `bedrock-agentcore-starter-toolkit` uses direct boto3 calls (no CDK/CFN). We'll adapt its patterns (zip packaging, S3 staging, runtime polling) into our own `aws/agentcore.py` module. This avoids coupling to an experimental package while leveraging proven implementation patterns.

### Decision 4: State file over CloudFormation
Using `.three-stars-state.json` (local state file) instead of CloudFormation gives us faster deployments, simpler code, and no template DSL overhead. The trade-off is that state can drift if resources are modified outside the tool, but for a simple 5-resource stack this is acceptable. The state file is human-readable JSON.

### Decision 5: src/ layout
Using `src/three_stars/` layout (not flat `three_stars/`) prevents accidental imports of the source tree during testing and follows modern Python best practices.

## 11. Risk Assessment

| Risk | Mitigation |
|------|-----------|
| AgentCore API may change (preview) | Isolate in `aws/agentcore.py`; easy to update one module |
| CloudFront distribution creation is slow (~5 min) | Show progress spinner with Rich; cache distribution for updates |
| State file drift | `status` command detects drift; `destroy` handles missing resources gracefully |
| User lacks IAM permissions | Early permission check with clear error messages |
| Large frontend directories | Upload with progress bar; set reasonable defaults |

## 12. Out of Scope (v1)

- Custom domain names (ACM certificate + Route53)
- Multiple environments (dev/staging/prod)
- Streaming agent responses (SSE/WebSocket)
- CI/CD integration
- Cost estimation
- Multi-region deployment
- Agent monitoring/logging dashboard

These can be added in v2 as the tool matures.

## 13. Success Criteria

A successful v1 implementation means:
1. `pip install three-stars` works
2. `three-stars init my-app && cd my-app && three-stars deploy` provisions all 3 resources
3. The CloudFront URL serves the frontend and routes `/api/*` to the agent
4. `three-stars status` shows resource health
5. `three-stars destroy` cleans up everything
6. All tests pass with moto mocks (no AWS account needed for testing)
