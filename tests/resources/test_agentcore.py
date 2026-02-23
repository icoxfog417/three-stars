"""Contract tests for AgentCore resource module (mock-based).

These tests verify our code's logic (API call patterns, state shape,
error handling) — NOT AWS behavior.  Integration tests with real AWS
live in tests/integration/test_agentcore.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from tests.conftest import make_test_names
from three_stars.config import AgentConfig, ProjectConfig
from three_stars.resources import agentcore
from three_stars.state import AgentCoreState

NAMES = make_test_names()


def _make_config(tmp_path: Path) -> ProjectConfig:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "agent.py").write_text("pass")
    (agent_dir / "requirements.txt").write_text("")
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "index.html").write_text("<html></html>")
    return ProjectConfig(
        name="test-app",
        region="us-east-1",
        agent=AgentConfig(source="agent", description="Test agent"),
        project_dir=tmp_path,
    )


def _make_existing_state() -> AgentCoreState:
    return AgentCoreState(
        iam_role_name=NAMES.agentcore_role,
        iam_role_arn=f"arn:aws:iam::123456789012:role/{NAMES.agentcore_role}",
        runtime_id="rt-existing",
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/rt-existing",
        endpoint_name="DEFAULT",
        endpoint_arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:endpoint/ep-existing",
        memory_id="mem-123",
        memory_name=NAMES.memory,
    )


# Common patches for all deploy tests — mock external dependencies that
# are not installable locally (bedrock_agentcore SDK, toolkit).
_DEPLOY_PATCHES = [
    "three_stars.resources.agentcore._create_iam_role",
    "three_stars.resources.agentcore._package_and_upload",
    "three_stars.resources.agentcore.MemoryClient",
    "three_stars.resources.agentcore.BedrockAgentCoreClient",
    "three_stars.resources.agentcore.retry_create_with_eventual_iam_consistency",
]


class TestDeployReturnsState:
    """deploy() returns AgentCoreState with all fields populated."""

    @patch("three_stars.resources.agentcore.retry_create_with_eventual_iam_consistency")
    @patch("three_stars.resources.agentcore.BedrockAgentCoreClient")
    @patch("three_stars.resources.agentcore.MemoryClient")
    @patch("three_stars.resources.agentcore._package_and_upload")
    @patch("three_stars.resources.agentcore._create_iam_role")
    def test_deploy_returns_state(
        self, mock_iam, mock_pkg, mock_mem_cls, mock_toolkit_cls, mock_retry, tmp_path
    ):
        ctx = MagicMock()
        ctx.account_id = "123456789012"
        config = _make_config(tmp_path)
        names = NAMES

        mock_iam.return_value = f"arn:aws:iam::123456789012:role/{names.agentcore_role}"
        mock_mem_cls.return_value.create_or_get_memory.return_value = {
            "id": "mem-456",
            "name": names.memory,
        }
        mock_retry.return_value = {
            "id": "rt-789",
            "arn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/rt-789",
        }
        toolkit_instance = mock_toolkit_cls.return_value
        toolkit_instance.wait_for_agent_endpoint_ready.return_value = (
            "arn:aws:bedrock-agentcore:us-east-1:123456789012:endpoint/ep-789"
        )

        state = agentcore.deploy(ctx, config, names, bucket_name="test-bucket")

        assert isinstance(state, AgentCoreState)
        assert state.iam_role_name == names.agentcore_role
        assert state.iam_role_arn == f"arn:aws:iam::123456789012:role/{names.agentcore_role}"
        assert state.runtime_id == "rt-789"
        assert state.runtime_arn.startswith("arn:")
        assert state.endpoint_name == "DEFAULT"
        assert state.endpoint_arn.startswith("arn:")
        assert state.memory_id == "mem-456"


class TestDeployUpdateIdempotent:
    """Re-deploy with existing state passes existing to toolkit (update path)."""

    @patch("three_stars.resources.agentcore.retry_create_with_eventual_iam_consistency")
    @patch("three_stars.resources.agentcore.BedrockAgentCoreClient")
    @patch("three_stars.resources.agentcore.MemoryClient")
    @patch("three_stars.resources.agentcore._package_and_upload")
    @patch("three_stars.resources.agentcore._create_iam_role")
    def test_deploy_update_idempotent(
        self, mock_iam, mock_pkg, mock_mem_cls, mock_toolkit_cls, mock_retry, tmp_path
    ):
        ctx = MagicMock()
        ctx.account_id = "123456789012"
        config = _make_config(tmp_path)
        names = NAMES
        existing = _make_existing_state()

        mock_iam.return_value = f"arn:aws:iam::123456789012:role/{names.agentcore_role}"
        mock_mem_cls.return_value.create_or_get_memory.return_value = {
            "id": "mem-123",
            "name": names.memory,
        }
        mock_retry.return_value = {
            "id": "rt-existing",
            "arn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/rt-existing",
        }
        toolkit_instance = mock_toolkit_cls.return_value
        toolkit_instance.wait_for_agent_endpoint_ready.return_value = (
            "arn:aws:bedrock-agentcore:us-east-1:123456789012:endpoint/ep-existing"
        )

        state = agentcore.deploy(ctx, config, names, bucket_name="test-bucket", existing=existing)

        assert state.runtime_id == "rt-existing"
        # Verify the toolkit received the existing agent_id for update
        assert mock_retry.called
        assert state.endpoint_name == "DEFAULT"


class TestDeployErrorHandling:
    """Handles errors from IAM or toolkit gracefully."""

    @patch("three_stars.resources.agentcore._create_iam_role")
    def test_iam_error_propagates(self, mock_iam, tmp_path):
        ctx = MagicMock()
        ctx.account_id = "123456789012"
        config = _make_config(tmp_path)
        names = NAMES

        mock_iam.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Not authorized"}},
            "CreateRole",
        )

        with pytest.raises(ClientError) as exc_info:
            agentcore.deploy(ctx, config, names, bucket_name="test-bucket")
        assert exc_info.value.response["Error"]["Code"] == "AccessDenied"

    @patch("three_stars.resources.agentcore.retry_create_with_eventual_iam_consistency")
    @patch("three_stars.resources.agentcore.BedrockAgentCoreClient")
    @patch("three_stars.resources.agentcore.MemoryClient")
    @patch("three_stars.resources.agentcore._package_and_upload")
    @patch("three_stars.resources.agentcore._create_iam_role")
    def test_endpoint_timeout_raises(
        self, mock_iam, mock_pkg, mock_mem_cls, mock_toolkit_cls, mock_retry, tmp_path
    ):
        ctx = MagicMock()
        ctx.account_id = "123456789012"
        config = _make_config(tmp_path)
        names = NAMES

        mock_iam.return_value = f"arn:aws:iam::123456789012:role/{names.agentcore_role}"
        mock_mem_cls.return_value.create_or_get_memory.return_value = {
            "id": "mem-456",
            "name": names.memory,
        }
        mock_retry.return_value = {
            "id": "rt-789",
            "arn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/rt-789",
        }
        # Simulate endpoint timeout — toolkit returns non-ARN string
        toolkit_instance = mock_toolkit_cls.return_value
        toolkit_instance.wait_for_agent_endpoint_ready.return_value = "TIMEOUT"

        with pytest.raises(TimeoutError, match="did not reach READY"):
            agentcore.deploy(ctx, config, names, bucket_name="test-bucket")


class TestDestroyCleanup:
    """destroy() removes all resources."""

    def test_destroy_cleans_up(self):
        ctx = MagicMock()
        state = _make_existing_state()

        # Mock the bedrock-agentcore-control client
        control_client = MagicMock()
        memory_client = MagicMock()

        ctx.client.return_value = control_client

        with patch("three_stars.resources.agentcore.MemoryClient") as mock_mem_cls:
            mock_mem_cls.return_value = memory_client
            with patch("three_stars.resources.agentcore._delete_iam_role") as mock_del_role:
                agentcore.destroy(ctx, state)

        # Verify memory was deleted
        memory_client.delete_memory_and_wait.assert_called_once_with(memory_id="mem-123")
        # Verify runtime was deleted
        control_client.delete_agent_runtime.assert_called_once_with(agentRuntimeId="rt-existing")
        # Verify IAM role was deleted
        mock_del_role.assert_called_once_with(ctx, NAMES.agentcore_role)


class TestDestroyIdempotent:
    """destroy() on already-deleted resources is a no-op."""

    def test_destroy_idempotent(self):
        ctx = MagicMock()
        state = _make_existing_state()

        control_client = MagicMock()
        # Simulate ResourceNotFoundException for runtime
        control_client.delete_agent_runtime.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Not found"}},
            "DeleteAgentRuntime",
        )
        ctx.client.return_value = control_client

        memory_client = MagicMock()
        memory_client.delete_memory_and_wait.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Not found"}},
            "DeleteMemory",
        )

        with (
            patch("three_stars.resources.agentcore.MemoryClient") as mock_mem_cls,
            patch("three_stars.resources.agentcore._delete_iam_role"),
        ):
            mock_mem_cls.return_value = memory_client
            # Should not raise
            agentcore.destroy(ctx, state)


class TestGetStatus:
    """get_status() returns correct status rows."""

    def test_get_status_ready(self):
        ctx = MagicMock()
        state = _make_existing_state()

        control_client = MagicMock()
        control_client.get_agent_runtime.return_value = {"status": "READY"}
        ctx.client.return_value = control_client

        with patch("three_stars.resources.agentcore.MemoryClient") as mock_mem_cls:
            mock_mem_cls.return_value.get_memory_status.return_value = "ACTIVE"
            rows = agentcore.get_status(ctx, state)

        assert len(rows) >= 3  # Runtime, Endpoint, Memory, Role
        assert "Ready" in rows[0].status
        assert rows[0].resource == "AgentCore Runtime"

    def test_get_status_not_found(self):
        ctx = MagicMock()
        state = _make_existing_state()

        control_client = MagicMock()
        control_client.get_agent_runtime.side_effect = Exception("Not found")
        ctx.client.return_value = control_client

        with patch("three_stars.resources.agentcore.MemoryClient") as mock_mem_cls:
            mock_mem_cls.return_value.get_memory_status.side_effect = Exception("Not found")
            rows = agentcore.get_status(ctx, state)

        assert "Not Found" in rows[0].status
