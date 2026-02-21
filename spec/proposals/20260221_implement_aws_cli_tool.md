# Proposal: Implement three-stars AWS CLI Tool

**Date**: 2026-02-21
**Author**: Claude Agent
**Status**: Proposed

## Background

The repository was created from the agentic-coding-template and needs its first feature: a Python CLI tool called "three-stars" that deploys AI-powered web applications to AWS using three core services (Bedrock AgentCore, CloudFront, CloudFront Functions).

## Proposal

Build a Python CLI tool with four commands:
- `three-stars init` — Scaffold a new project
- `three-stars deploy` — Deploy agent + frontend to AWS
- `three-stars status` — Show deployment health
- `three-stars destroy` — Tear down all resources

The tool provisions exactly 5 AWS resources: S3 bucket, AgentCore Runtime, CloudFront Distribution, CloudFront Function, and IAM Role.

Tech stack: Python 3.11+, Click, Rich, boto3, PyYAML.

See `PLAN.md` for full architecture, module design, and sprint breakdown.

## Impact

- **Requirements**: Complete rewrite — define personas, functional requirements, user stories for the CLI tool
- **Design**: Complete rewrite — define architecture, data models, component design, deployment flow
- **Tasks**: Complete rewrite — 25 tasks across 5 sprints (Foundation, AWS Core, Orchestration, Init+Polish, Docs+Release)

## Alternatives Considered

1. **TypeScript + CDK**: Rejected — AgentCore SDK is Python-only; CDK adds heavyweight CloudFormation dependency
2. **Depend on bedrock-agentcore-starter-toolkit**: Rejected — experimental package, adds Typer dependency, prefer vendoring patterns
3. **CloudFormation/SAM templates**: Rejected — slower deploys, template DSL overhead, overkill for 5 resources

## Implementation Plan

See `PLAN.md` for detailed sprint breakdown with 25 tasks across 5 sprints.
