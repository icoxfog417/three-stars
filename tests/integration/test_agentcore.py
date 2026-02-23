"""Integration tests for AgentCore resource module.

These tests deploy real resources to AWS and verify the full lifecycle:
packaging, deploy, invoke, update, status, and destroy.

Run with:  uv run pytest tests/integration/test_agentcore.py -v -s
"""

from __future__ import annotations

import contextlib
import json
import shutil
import uuid

import boto3
import pytest

from three_stars.config import AgentConfig, ProjectConfig
from three_stars.naming import compute_names
from three_stars.resources import agentcore
from three_stars.resources._base import AWSContext

REGION = "us-east-1"
PROJECT_NAME = "sss-e2e-test"


@pytest.fixture(scope="module")
def aws_ctx():
    return AWSContext(boto3.Session(region_name=REGION))


@pytest.fixture(scope="module")
def agent_dir(tmp_path_factory):
    """Create a minimal agent directory for testing."""
    d = tmp_path_factory.mktemp("agent_project")
    agent = d / "agent"
    agent.mkdir()
    (agent / "agent.py").write_text(
        "from bedrock_agentcore.runtime import BedrockAgentCoreApp\n"
        "app = BedrockAgentCoreApp()\n"
        "@app.entrypoint\n"
        "def handler(payload):\n"
        "    return {\"message\": f\"echo: {payload.get('prompt', '')}\"}\n"
        'if __name__ == "__main__":\n'
        "    app.run()\n"
    )
    (agent / "requirements.txt").write_text("bedrock-agentcore\n")
    return d


@pytest.fixture(scope="module")
def config_and_names(agent_dir, aws_ctx):
    config = ProjectConfig(
        name=PROJECT_NAME,
        region=REGION,
        agent=AgentConfig(source="agent", description="E2E test agent"),
        project_dir=agent_dir,
    )
    names = compute_names(config, aws_ctx.account_id)
    return config, names


@pytest.fixture(scope="module")
def s3_bucket(aws_ctx, config_and_names):
    """Create and tear down an S3 bucket for the test module."""
    _, names = config_and_names
    bucket_name = names.bucket
    s3 = aws_ctx.client("s3")
    with contextlib.suppress(s3.exceptions.BucketAlreadyOwnedByYou):
        s3.create_bucket(Bucket=bucket_name)
    yield bucket_name
    # Cleanup
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket_name):
            for obj in page.get("Contents", []):
                s3.delete_object(Bucket=bucket_name, Key=obj["Key"])
        s3.delete_bucket(Bucket=bucket_name)
    except Exception:
        pass


@pytest.fixture(scope="module")
def deployed_state(aws_ctx, config_and_names, s3_bucket):
    """Deploy AgentCore resources; tear down after all tests in module."""
    config, names = config_and_names
    state = agentcore.deploy(aws_ctx, config, names, bucket_name=s3_bucket)
    yield state
    # Cleanup — destroy the runtime (IAM role cleaned up here too)
    with contextlib.suppress(Exception):
        agentcore.destroy(aws_ctx, state)
    # Clean up packaging cache created inside agent dir
    cache_dir = config.project_dir / "agent" / ".bedrock_agentcore"
    if cache_dir.exists():
        shutil.rmtree(cache_dir, ignore_errors=True)


class TestDeploy:
    def test_runtime_id_returned(self, deployed_state):
        assert deployed_state.runtime_id
        assert deployed_state.runtime_arn.startswith("arn:")

    def test_endpoint_is_default(self, deployed_state):
        assert deployed_state.endpoint_name == "DEFAULT"
        assert deployed_state.endpoint_arn.startswith("arn:")

    def test_iam_role_created(self, deployed_state):
        assert deployed_state.iam_role_name
        assert deployed_state.iam_role_arn.startswith("arn:")


class TestInvoke:
    def test_invoke_returns_echo(self, aws_ctx, deployed_state):
        """Invoke the deployed endpoint and verify the agent responds."""
        data_client = aws_ctx.client("bedrock-agentcore")
        response = data_client.invoke_agent_runtime(
            agentRuntimeArn=deployed_state.runtime_arn,
            qualifier="DEFAULT",
            runtimeSessionId=str(uuid.uuid4()),
            payload=json.dumps({"prompt": "ping"}),
            contentType="application/json",
        )
        body = json.loads(response["response"].read().decode("utf-8"))
        assert body["message"] == "echo: ping"


class TestStatus:
    def test_runtime_ready(self, aws_ctx, deployed_state):
        rows = agentcore.get_status(aws_ctx, deployed_state)
        runtime_row = rows[0]
        assert "Ready" in runtime_row.status

    def test_endpoint_ready(self, aws_ctx, deployed_state):
        rows = agentcore.get_status(aws_ctx, deployed_state)
        endpoint_row = rows[1]
        assert "Ready" in endpoint_row.status


class TestUpdate:
    def test_update_preserves_runtime_id(
        self, aws_ctx, config_and_names, s3_bucket, deployed_state
    ):
        config, names = config_and_names
        updated = agentcore.deploy(
            aws_ctx, config, names, bucket_name=s3_bucket, existing=deployed_state
        )
        assert updated.runtime_id == deployed_state.runtime_id
        assert updated.endpoint_name == "DEFAULT"
