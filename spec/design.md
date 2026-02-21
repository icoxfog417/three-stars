# Design Specification

**Project**: three-stars
**Version**: 0.1.0
**Last Updated**: 2026-02-21

## 1. Architecture Overview

### 1.1 High-Level Architecture

```
User CLI                      AWS Cloud
┌──────────────┐    ┌──────────────────────────────────────────┐
│ three-stars  │    │                                          │
│  CLI (Click) │───▶│  S3 Bucket (frontend static files)      │
│              │    │       ▲                                  │
│  config.py   │    │       │ OAC                              │
│  deploy.py   │    │  CloudFront Distribution                 │
│  destroy.py  │    │   ├── /* → S3 origin                    │
│  status.py   │    │   └── /api/* → CF Function → AgentCore  │
│              │    │                                          │
│  aws/        │    │  CloudFront Function (JS router)        │
│  ├─session   │    │                                          │
│  ├─s3        │    │  Bedrock AgentCore Runtime               │
│  ├─cloudfront│    │   └── agent.py + Bedrock model access   │
│  ├─cf_function│   │                                          │
│  └─agentcore │    │  IAM Role (execution permissions)       │
└──────────────┘    └──────────────────────────────────────────┘
```

### 1.2 Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| CLI | Click 8.x | Command parsing, help text, argument validation |
| Terminal UX | Rich 13.x | Progress bars, tables, colored output |
| AWS SDK | boto3 1.35+ | All AWS API operations |
| Configuration | PyYAML 6.x | Parse three-stars.yml |
| Testing | pytest + moto | Unit tests with AWS mocks |
| Linting | ruff | Linting and formatting |
| Packaging | hatchling | Build backend for pyproject.toml |

## 2. Data Models

### 2.1 ProjectConfig

Parsed from `three-stars.yml`.

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| name | str | Project name (used in resource naming) | required |
| region | str | AWS region | us-east-1 |
| agent.source | str | Path to agent code directory | ./agent |
| agent.model | str | Bedrock model ID | anthropic.claude-sonnet-4-20250514 |
| agent.description | str | Agent description | "" |
| agent.memory | int | Runtime memory in MB | 512 |
| app.source | str | Path to frontend directory | ./app |
| app.index | str | Index document filename | index.html |
| app.error | str | Error document filename | None |
| api.prefix | str | URL prefix for API routing | /api |

### 2.2 DeploymentState

Stored in `.three-stars-state.json`.

| Field | Type | Description |
|-------|------|-------------|
| version | int | State file schema version (1) |
| project_name | str | Project name from config |
| region | str | AWS region used |
| deployed_at | str | ISO 8601 timestamp |
| resources.s3_bucket | str | S3 bucket name |
| resources.agentcore_runtime_id | str | AgentCore runtime ID |
| resources.agentcore_endpoint | str | AgentCore invoke URL |
| resources.cloudfront_distribution_id | str | CloudFront distribution ID |
| resources.cloudfront_function_name | str | CloudFront Function name |
| resources.cloudfront_domain | str | CloudFront domain name (*.cloudfront.net) |
| resources.iam_role_arn | str | IAM role ARN |

## 3. API Design

### 3.1 CLI Commands

| Command | Arguments | Flags | Description |
|---------|-----------|-------|-------------|
| `init` | `[name]` | `--template` | Scaffold new project |
| `deploy` | `[project_dir]` | `--region`, `--profile`, `--yes` | Deploy to AWS |
| `status` | `[project_dir]` | `--region`, `--profile` | Show deployment status |
| `destroy` | `[project_dir]` | `--region`, `--profile`, `--yes` | Tear down resources |

### 3.2 AWS API Calls

| Module | AWS Service | Operations Used |
|--------|------------|-----------------|
| agentcore.py | Bedrock AgentCore | CreateAgentRuntime, GetAgentRuntime, UpdateAgentRuntime, DeleteAgentRuntime |
| s3.py | S3 | CreateBucket, PutObject, DeleteObject, ListObjectsV2, DeleteBucket, PutBucketPolicy |
| cloudfront.py | CloudFront | CreateDistribution, GetDistribution, UpdateDistribution, DeleteDistribution, CreateOriginAccessControl |
| cf_function.py | CloudFront | CreateFunction, DescribeFunction, UpdateFunction, PublishFunction, DeleteFunction |
| session.py | STS | GetCallerIdentity |

## 4. Component Design

### 4.1 CLI Layer (`cli.py`)

**Purpose**: Entry point, argument parsing, user interaction
**Inputs**: Command-line arguments and flags
**Outputs**: Calls orchestration modules, displays results via Rich

### 4.2 Config Loader (`config.py`)

**Purpose**: Load, validate, and merge configuration
**Inputs**: `three-stars.yml` path, CLI flag overrides
**Outputs**: `ProjectConfig` dataclass
**Validation**: Required fields present, paths exist, valid AWS region format

### 4.3 Deploy Orchestrator (`deploy.py`)

**Purpose**: Coordinate deployment of all resources in order
**Inputs**: `ProjectConfig`, boto3 session
**Outputs**: `DeploymentState` saved to disk
**Sequence**:
1. Create IAM role (if not exists)
2. Package and deploy agent to AgentCore
3. Create S3 bucket and upload frontend
4. Create CloudFront Function with AgentCore endpoint
5. Create CloudFront Distribution with S3 origin + function
6. Save state file

### 4.4 Destroy Orchestrator (`destroy.py`)

**Purpose**: Tear down all resources in reverse order
**Inputs**: `DeploymentState`
**Sequence** (reverse of deploy):
1. Delete CloudFront Distribution (disable first, wait, then delete)
2. Delete CloudFront Function
3. Delete AgentCore Runtime
4. Empty and delete S3 Bucket
5. Delete IAM Role
6. Remove state file

### 4.5 Status Reporter (`status.py`)

**Purpose**: Query and display resource health
**Inputs**: `DeploymentState`, boto3 session
**Outputs**: Rich table with resource statuses

### 4.6 AWS Modules (`aws/`)

Each module is a thin wrapper around boto3 calls for one AWS service. Modules are stateless — they receive a boto3 session and return results. Error handling wraps `botocore.exceptions.ClientError` with user-friendly messages.

## 5. Security Architecture

- **Authentication**: Uses standard AWS credential chain (env vars, `~/.aws/credentials`, IAM role)
- **Authorization**: IAM role created for AgentCore follows least-privilege (only `bedrock:InvokeModel`)
- **Data encryption**: S3 bucket uses default encryption (SSE-S3); CloudFront uses HTTPS only
- **Access control**: S3 bucket is private; accessed only via CloudFront Origin Access Control (OAC)

## 6. Error Handling

- **AWS API errors**: Caught via `botocore.exceptions.ClientError`, mapped to user-friendly messages
- **Missing credentials**: Detected early, suggest `aws configure`
- **Invalid config**: Validated before any AWS calls, with specific field-level error messages
- **Partial deployment failure**: State file written after each successful resource creation, enabling resume/cleanup
- **Resource not found on destroy**: Logged as warning, continue with remaining resources

## 7. Deployment Architecture

The tool itself is a Python package installed via pip. It deploys user applications to AWS — the tool does not deploy itself.

```
pip install three-stars
  └── Installs CLI entry point `three-stars`

three-stars deploy
  └── Creates AWS resources via boto3 API calls
      ├── S3 bucket + objects
      ├── AgentCore runtime
      ├── CloudFront distribution
      ├── CloudFront function
      └── IAM role
```
