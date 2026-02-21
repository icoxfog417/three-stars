# Requirements Specification

**Project**: three-stars
**Version**: 0.1.0
**Last Updated**: 2026-02-21

## 1. Overview

three-stars is a CLI tool that deploys AI-powered web applications to AWS with a single command. It provisions a Bedrock AgentCore runtime (AI backend), an S3-backed CloudFront distribution (frontend), and a CloudFront Function (API routing) — the "three stars" of the deployment.

## 2. User Personas

### 2.1 AI App Developer
- **Role**: Developer building AI-powered web applications using Amazon Bedrock models
- **Goals**:
  - Deploy an AI agent + frontend with minimal AWS knowledge
  - Iterate quickly on agent code and frontend without manual infrastructure management
  - Tear down resources cleanly when done
- **Technical Level**: Developer familiar with Python and basic AWS credentials, but not an AWS infrastructure expert

## 3. Functional Requirements

### 3.1 Project Initialization

- **REQ-INIT-001**: The CLI shall scaffold a new project directory with a config file, frontend template, and agent code template
- **REQ-INIT-002**: The scaffolded project shall include a working `three-stars.yml` configuration file with sensible defaults

### 3.2 Deployment

- **REQ-DEPLOY-001**: The CLI shall deploy a complete AI web application from a single `three-stars deploy` command
- **REQ-DEPLOY-002**: The CLI shall create an S3 bucket and upload frontend static files from the `app/` directory
- **REQ-DEPLOY-003**: The CLI shall create a Bedrock AgentCore runtime with the agent code from the `agent/` directory
- **REQ-DEPLOY-004**: The CLI shall create a CloudFront distribution with the S3 bucket as origin
- **REQ-DEPLOY-005**: The CLI shall create a CloudFront Function that routes `/api/*` requests to the AgentCore endpoint
- **REQ-DEPLOY-006**: The CLI shall create an IAM execution role for the AgentCore runtime
- **REQ-DEPLOY-007**: The CLI shall save deployment state to `.three-stars-state.json` for subsequent operations
- **REQ-DEPLOY-008**: The CLI shall support updating existing deployments (detect existing state and update resources)
- **REQ-DEPLOY-009**: The CLI shall display a progress indicator during deployment
- **REQ-DEPLOY-010**: The CLI shall print the CloudFront URL upon successful deployment

### 3.3 Status

- **REQ-STATUS-001**: The CLI shall display the current deployment status of all resources
- **REQ-STATUS-002**: The CLI shall query live AWS resource states (not just local state)

### 3.4 Teardown

- **REQ-DESTROY-001**: The CLI shall tear down all deployed resources in the correct order
- **REQ-DESTROY-002**: The CLI shall require confirmation before destroying resources (overridable with `--yes`)
- **REQ-DESTROY-003**: The CLI shall handle partially-deployed states gracefully (skip missing resources)

### 3.5 Configuration

- **REQ-CONFIG-001**: The CLI shall read project configuration from `three-stars.yml`
- **REQ-CONFIG-002**: The CLI shall support `--region` and `--profile` flags to override AWS settings
- **REQ-CONFIG-003**: The CLI shall validate configuration and provide clear error messages for invalid configs

## 4. Non-Functional Requirements

### 4.1 Performance
- **REQ-NF-001**: Deploy command shall complete within 10 minutes (CloudFront propagation is the bottleneck)
- **REQ-NF-002**: Status and destroy commands shall complete within 30 seconds

### 4.2 Security
- **REQ-NF-010**: The CLI shall never store or log AWS credentials
- **REQ-NF-011**: IAM roles shall follow least-privilege principle
- **REQ-NF-012**: S3 bucket shall not be publicly accessible (accessed only via CloudFront OAC)

### 4.3 Usability
- **REQ-NF-020**: The CLI shall provide colored, formatted terminal output using Rich
- **REQ-NF-021**: Error messages shall suggest actionable fixes (e.g., "Run `aws configure` to set up credentials")
- **REQ-NF-022**: The CLI shall be installable via `pip install three-stars`

### 4.4 Reliability
- **REQ-NF-030**: The CLI shall handle AWS API errors with clear messages
- **REQ-NF-031**: The CLI shall support partial rollback if deployment fails mid-way

## 5. User Stories

### US-001: First Deployment
**As a** developer, **I want to** run `three-stars init && three-stars deploy`, **so that** I have a working AI web app URL in minutes without configuring AWS resources manually.

**Acceptance Criteria**:
- [ ] `three-stars init my-app` creates a project directory with config, frontend, and agent code
- [ ] `three-stars deploy` in that directory deploys all resources and prints a URL
- [ ] Visiting the URL shows the frontend and `/api/` routes to the agent

### US-002: Iterating on Code
**As a** developer, **I want to** run `three-stars deploy` again after changing my code, **so that** my changes are deployed without recreating all resources.

**Acceptance Criteria**:
- [ ] Running deploy with existing state updates resources instead of creating duplicates
- [ ] Frontend file changes are uploaded to S3
- [ ] Agent code changes update the AgentCore runtime

### US-003: Cleanup
**As a** developer, **I want to** run `three-stars destroy`, **so that** all AWS resources are deleted and I stop incurring costs.

**Acceptance Criteria**:
- [ ] All 5 resource types are deleted
- [ ] State file is removed after successful teardown
- [ ] Confirmation is required before deletion

## 6. Constraints and Dependencies

- Requires Python 3.11+
- Requires valid AWS credentials with permissions for S3, CloudFront, IAM, and Bedrock AgentCore
- Amazon Bedrock AgentCore is in preview — API surface may change
- CloudFront distribution creation takes ~5 minutes for propagation

## 7. Acceptance Criteria for MVP

- [ ] `three-stars init` scaffolds a valid project
- [ ] `three-stars deploy` creates all 5 AWS resources and returns a working URL
- [ ] `three-stars status` displays resource health
- [ ] `three-stars destroy` tears down all resources
- [ ] All tests pass without an AWS account (moto mocks)
- [ ] `pip install` from the repository works
