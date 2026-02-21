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
│  state.py    │    │  CloudFront Distribution                 │
│  naming.py   │    │   ├── /* → S3 origin                    │
│  deploy.py   │    │   └── /api/* → Lambda → AgentCore       │
│  destroy.py  │    │                                          │
│  status.py   │    │  Lambda@Edge (SHA256 for OAC)           │
│              │    │                                          │
│  resources/  │    │  Lambda Bridge (API → AgentCore)        │
│  ├─agentcore │    │                                          │
│  ├─storage   │    │  Bedrock AgentCore Runtime               │
│  ├─api_bridge│    │   └── agent.py + Bedrock model access   │
│  ├─edge      │    │                                          │
│  └─cdn       │    │  IAM Roles (per-resource)               │
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

Stored in `.three-stars-state.json`. Uses typed dataclasses for compile-time safety.

**Top-level state:**

| Field | Type | Description |
|-------|------|-------------|
| version | int | State file schema version (1) |
| project_name | str | Project name from config |
| region | str | AWS region used |
| deployed_at | str | ISO 8601 timestamp |
| updated_at | str \| None | Last update timestamp |
| agentcore | AgentCoreState \| None | AgentCore resource state |
| storage | StorageState \| None | S3 storage resource state |
| api_bridge | ApiBridgeState \| None | Lambda bridge resource state |
| edge | EdgeState \| None | Lambda@Edge resource state |
| cdn | CdnState \| None | CloudFront CDN resource state |

**AgentCoreState:**

| Field | Type | Description |
|-------|------|-------------|
| iam_role_name | str | IAM role name |
| iam_role_arn | str | IAM role ARN |
| runtime_id | str | AgentCore runtime ID |
| runtime_arn | str | AgentCore runtime ARN |
| endpoint_name | str | AgentCore endpoint name |
| endpoint_arn | str | AgentCore endpoint ARN |

**StorageState:**

| Field | Type | Description |
|-------|------|-------------|
| s3_bucket | str | S3 bucket name |

**ApiBridgeState:**

| Field | Type | Description |
|-------|------|-------------|
| role_name | str | Lambda execution role name |
| role_arn | str | Lambda execution role ARN |
| function_name | str | Lambda function name |
| function_arn | str | Lambda function ARN |
| function_url | str | Lambda function URL |

**EdgeState:**

| Field | Type | Description |
|-------|------|-------------|
| role_name | str | Lambda@Edge role name |
| role_arn | str | Lambda@Edge role ARN |
| function_name | str | Lambda@Edge function name |
| function_arn | str | Lambda@Edge function ARN (versioned) |

**CdnState:**

| Field | Type | Description |
|-------|------|-------------|
| distribution_id | str | CloudFront distribution ID |
| domain | str | CloudFront domain name (*.cloudfront.net) |
| arn | str | CloudFront distribution ARN |
| oac_id | str | S3 Origin Access Control ID |
| lambda_oac_id | str | Lambda Origin Access Control ID |

### 2.3 ResourceNames

Computed from config + account ID. Frozen dataclass — immutable after creation.

| Field | Type | Description |
|-------|------|-------------|
| prefix | str | Base resource prefix (from project name) |
| bucket | str | S3 bucket name (prefix + account hash) |
| agentcore_role | str | AgentCore IAM role name |
| agent_name | str | AgentCore runtime name |
| endpoint_name | str | AgentCore endpoint name |
| lambda_role | str | Lambda execution role name |
| lambda_function | str | Lambda function name |
| edge_role | str | Lambda@Edge role name |
| edge_function | str | Lambda@Edge function name |

## 3. API Design

### 3.1 CLI Commands

| Command | Arguments | Flags | Description |
|---------|-----------|-------|-------------|
| `init` | `[name]` | `--template` | Scaffold new project |
| `deploy` | `[project_dir]` | `--region`, `--profile`, `--yes` | Deploy to AWS |
| `status` | `[project_dir]` | `--region`, `--profile` | Show deployment status |
| `destroy` | `[project_dir]` | `--region`, `--profile`, `--yes` | Tear down resources |

### 3.2 AWS API Calls

| Resource Module | AWS Services | Operations Used |
|----------------|-------------|-----------------|
| resources/agentcore.py | IAM, Bedrock AgentCore, S3 | CreateRole, CreateAgentRuntime, GetAgentRuntime, DeleteAgentRuntime, CreateAgentRuntimeEndpoint, DeleteAgentRuntimeEndpoint |
| resources/storage.py | S3 | CreateBucket, PutObject, DeleteObject, ListObjectsV2, DeleteBucket, PutBucketPolicy |
| resources/api_bridge.py | IAM, Lambda | CreateRole, CreateFunction, CreateFunctionUrlConfig, DeleteFunction, DeleteRole |
| resources/edge.py | IAM, Lambda (us-east-1) | CreateRole, CreateFunction, DeleteFunction, DeleteRole |
| resources/cdn.py | CloudFront, Lambda | CreateDistribution, GetDistribution, UpdateDistribution, DeleteDistribution, CreateOriginAccessControl, AddPermission |
| resources/_base.py | STS | GetCallerIdentity |

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

### 4.3 State Manager (`state.py`)

**Purpose**: Typed deployment state with serialization
**Types**: `DeploymentState`, `AgentCoreState`, `StorageState`, `ApiBridgeState`, `EdgeState`, `CdnState`
**Serialization**: `dataclasses.asdict()` for save, nested dict reconstruction for load
**Key principle**: All state access uses typed attribute access — no dynamic dictionary keys

### 4.4 Naming (`naming.py`)

**Purpose**: Compute all AWS resource names from config and account ID
**Inputs**: `ProjectConfig`, AWS account ID
**Outputs**: `ResourceNames` frozen dataclass
**Key principle**: Single source of truth for all resource naming conventions

### 4.5 Deploy Orchestrator (`deploy.py`)

**Purpose**: Coordinate deployment of all resources in order, threading cross-resource data
**Inputs**: `ProjectConfig`, optional AWS profile
**Outputs**: `DeploymentState` saved to disk
**Design**: The orchestrator is the explicit dependency manager — it passes typed outputs from earlier resources as inputs to later ones. Resource modules never reference each other.
**Sequence**:
1. AgentCore: IAM role + runtime + endpoint → `AgentCoreState`
2. Storage: S3 bucket + frontend upload → `StorageState`
3. API Bridge: Lambda function + role (needs `runtime_arn`) → `ApiBridgeState`
4. Edge: Lambda@Edge function + role → `EdgeState`
5. CDN: CloudFront distribution + OACs (needs bucket, function URL, edge ARN) → `CdnState`

### 4.6 Destroy Orchestrator (`destroy.py`)

**Purpose**: Tear down all resources in reverse order
**Inputs**: `DeploymentState` (typed)
**Design**: Each module receives only its own typed state (e.g., `CdnState`). `None` check handles partially-deployed stacks.
**Sequence** (reverse of deploy):
1. CDN: disable and delete distribution + OACs
2. Edge: delete Lambda@Edge function + role
3. API Bridge: delete Lambda function + role
4. Storage: empty and delete S3 bucket
5. AgentCore: delete endpoint + runtime + IAM role

### 4.7 Status Reporter (`status.py`)

**Purpose**: Query and display resource health
**Inputs**: `DeploymentState` (typed), boto3 session
**Outputs**: Rich table with resource statuses
**Design**: Each module's `get_status()` receives its own typed state

### 4.8 Resource Modules (`resources/`)

Each module owns a cohesive resource group (e.g., a Lambda function + its IAM role). Modules expose three plain functions: `deploy()`, `destroy()`, `get_status()`. Each `deploy()` returns a typed state dataclass. Modules are stateless and never import each other — all cross-resource wiring is in the orchestrator. Error handling wraps `botocore.exceptions.ClientError` with user-friendly messages.

| Module | Resource Group | Dependencies (from orchestrator) |
|--------|---------------|----------------------------------|
| `agentcore.py` | IAM role + runtime + endpoint | None |
| `storage.py` | S3 bucket + frontend upload | None |
| `api_bridge.py` | Lambda function + IAM role + function URL | `agentcore.runtime_arn` |
| `edge.py` | Lambda@Edge function + IAM role (us-east-1) | None |
| `cdn.py` | CloudFront distribution + OACs + bucket policy | `storage.s3_bucket`, `api_bridge.function_url`, `api_bridge.function_name`, `edge.function_arn` |

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
  └── Orchestrator creates resources via resource modules
      ├── agentcore: IAM role + runtime + endpoint
      ├── storage: S3 bucket + frontend files
      ├── api_bridge: Lambda function + IAM role + function URL
      ├── edge: Lambda@Edge function + IAM role
      └── cdn: CloudFront distribution + OACs
```
