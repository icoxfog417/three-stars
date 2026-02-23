"""Orchestration tests for the deploy command.

Tests verify the deploy workflow order, state persistence, partial failure
handling, update detection, and force mode — all with mocked resource modules.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from three_stars.config import ProjectConfig
from three_stars.state import (
    AgentCoreState,
    CdnState,
    DeploymentState,
    EdgeState,
    StorageState,
    create_initial_state,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config(tmp_path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "agent.py").write_text("pass")
    (agent_dir / "requirements.txt").write_text("")
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "index.html").write_text("<html></html>")
    return ProjectConfig(name="test-app", region="us-east-1", project_dir=tmp_path)


@pytest.fixture
def names():
    from tests.conftest import make_test_names

    return make_test_names("test-app")


# ---------------------------------------------------------------------------
# Shared mock return values
# ---------------------------------------------------------------------------


def _storage_state():
    return StorageState(s3_bucket="test-bucket")


def _agentcore_state():
    return AgentCoreState(
        iam_role_name="test-role",
        iam_role_arn="arn:aws:iam::123:role/test",
        runtime_id="rt-123",
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/rt-123",
        endpoint_name="DEFAULT",
        endpoint_arn="arn:aws:bedrock-agentcore:us-east-1:123:endpoint/ep-123",
        memory_id="mem-123",
        memory_name="test_memory",
    )


def _edge_state():
    return EdgeState(
        role_name="test-edge-role",
        role_arn="arn:aws:iam::123:role/edge",
        function_name="test-edge-fn",
        function_arn="arn:aws:lambda:us-east-1:123:function/edge:1",
    )


def _cdn_state():
    return CdnState(
        distribution_id="E1234",
        domain="d1234.cloudfront.net",
        arn="arn:aws:cloudfront::123:distribution/E1234",
        oac_id="oac-123",
        lambda_oac_id="",
    )


def _setup_mocks(storage_mock, agentcore_mock, edge_mock, cdn_mock):
    """Wire up standard return values on resource module mocks."""
    storage_mock.deploy.return_value = _storage_state()
    agentcore_mock.deploy.return_value = _agentcore_state()
    edge_mock.deploy.return_value = _edge_state()
    cdn_mock.deploy.return_value = _cdn_state()
    cdn_mock.wait_for_deployed.return_value = "Deployed"


# Decorator stack applied to every test — order matters (bottom-up matches
# positional args left-to-right).
_COMMON_PATCHES = [
    patch("three_stars.deploy.compute_names"),
    patch("three_stars.deploy.AWSContext"),
    patch("three_stars.deploy.backup_state"),
    patch("three_stars.deploy.load_state"),
    patch("three_stars.deploy.save_state"),
    patch("three_stars.deploy.cdn"),
    patch("three_stars.deploy.edge"),
    patch("three_stars.deploy.agentcore"),
    patch("three_stars.deploy.storage"),
    patch("three_stars.deploy._print_health_check"),
]


def _apply_patches(func):
    """Apply the common patch stack to *func*."""
    wrapped = func
    for p in _COMMON_PATCHES:
        wrapped = p(wrapped)
    return wrapped


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDeployStepOrder:
    """Verify resource modules are called in the correct order."""

    @patch("three_stars.deploy._print_health_check")
    @patch("three_stars.deploy.storage")
    @patch("three_stars.deploy.agentcore")
    @patch("three_stars.deploy.edge")
    @patch("three_stars.deploy.cdn")
    @patch("three_stars.deploy.save_state")
    @patch("three_stars.deploy.load_state")
    @patch("three_stars.deploy.backup_state")
    @patch("three_stars.deploy.AWSContext")
    @patch("three_stars.deploy.compute_names")
    def test_deploy_step_order(
        self,
        compute_names_mock,
        aws_ctx_mock,
        backup_mock,
        load_state_mock,
        save_state_mock,
        cdn_mock,
        edge_mock,
        agentcore_mock,
        storage_mock,
        health_mock,
        config,
        names,
    ):
        from three_stars.deploy import run_deploy

        compute_names_mock.return_value = names
        ctx_instance = MagicMock()
        ctx_instance.account_id = "123456789012"
        aws_ctx_mock.create.return_value = ctx_instance
        load_state_mock.return_value = None  # fresh deploy

        _setup_mocks(storage_mock, agentcore_mock, edge_mock, cdn_mock)

        # Track call order using a shared list
        call_order: list[str] = []
        storage_mock.deploy.side_effect = lambda *a, **kw: (
            call_order.append("storage.deploy"),
            _storage_state(),
        )[1]
        agentcore_mock.deploy.side_effect = lambda *a, **kw: (
            call_order.append("agentcore.deploy"),
            _agentcore_state(),
        )[1]
        edge_mock.deploy.side_effect = lambda *a, **kw: (
            call_order.append("edge.deploy"),
            _edge_state(),
        )[1]
        cdn_mock.deploy.side_effect = lambda *a, **kw: (
            call_order.append("cdn.deploy"),
            _cdn_state(),
        )[1]
        agentcore_mock.set_resource_policy.side_effect = lambda *a, **kw: call_order.append(
            "agentcore.set_resource_policy"
        )

        run_deploy(config)

        assert call_order == [
            "storage.deploy",
            "agentcore.deploy",
            "edge.deploy",
            "cdn.deploy",
            "agentcore.set_resource_policy",
        ]


class TestDeployStatePersistence:
    """Verify save_state is called after every resource step."""

    @patch("three_stars.deploy._print_health_check")
    @patch("three_stars.deploy.storage")
    @patch("three_stars.deploy.agentcore")
    @patch("three_stars.deploy.edge")
    @patch("three_stars.deploy.cdn")
    @patch("three_stars.deploy.save_state")
    @patch("three_stars.deploy.load_state")
    @patch("three_stars.deploy.backup_state")
    @patch("three_stars.deploy.AWSContext")
    @patch("three_stars.deploy.compute_names")
    def test_deploy_state_after_each_step(
        self,
        compute_names_mock,
        aws_ctx_mock,
        backup_mock,
        load_state_mock,
        save_state_mock,
        cdn_mock,
        edge_mock,
        agentcore_mock,
        storage_mock,
        health_mock,
        config,
        names,
    ):
        from three_stars.deploy import run_deploy

        compute_names_mock.return_value = names
        ctx_instance = MagicMock()
        ctx_instance.account_id = "123456789012"
        aws_ctx_mock.create.return_value = ctx_instance
        load_state_mock.return_value = None

        _setup_mocks(storage_mock, agentcore_mock, edge_mock, cdn_mock)

        run_deploy(config)

        # 5 save_state calls: storage, agentcore, edge, cdn, set_resource_policy
        assert save_state_mock.call_count == 5

        # Every call should receive (project_dir, state)
        for c in save_state_mock.call_args_list:
            assert c[0][0] == config.project_dir
            assert isinstance(c[0][1], DeploymentState)


class TestDeployPartialFailure:
    """Verify state integrity when a mid-pipeline step fails."""

    @patch("three_stars.deploy._print_health_check")
    @patch("three_stars.deploy.storage")
    @patch("three_stars.deploy.agentcore")
    @patch("three_stars.deploy.edge")
    @patch("three_stars.deploy.cdn")
    @patch("three_stars.deploy.save_state")
    @patch("three_stars.deploy.load_state")
    @patch("three_stars.deploy.backup_state")
    @patch("three_stars.deploy.AWSContext")
    @patch("three_stars.deploy.compute_names")
    def test_deploy_partial_failure(
        self,
        compute_names_mock,
        aws_ctx_mock,
        backup_mock,
        load_state_mock,
        save_state_mock,
        cdn_mock,
        edge_mock,
        agentcore_mock,
        storage_mock,
        health_mock,
        config,
        names,
    ):
        from three_stars.deploy import run_deploy

        compute_names_mock.return_value = names
        ctx_instance = MagicMock()
        ctx_instance.account_id = "123456789012"
        aws_ctx_mock.create.return_value = ctx_instance
        load_state_mock.return_value = None

        _setup_mocks(storage_mock, agentcore_mock, edge_mock, cdn_mock)
        edge_mock.deploy.side_effect = RuntimeError("Lambda creation failed")

        with pytest.raises(RuntimeError, match="Lambda creation failed"):
            run_deploy(config)

        # save_state should have been called twice: after storage and after agentcore
        assert save_state_mock.call_count == 2

        # Inspect the last saved state — it should have storage + agentcore but not edge/cdn
        last_state: DeploymentState = save_state_mock.call_args_list[-1][0][1]
        assert last_state.storage is not None
        assert last_state.agentcore is not None
        assert last_state.edge is None
        assert last_state.cdn is None


class TestDeployUpdateDetection:
    """Verify that an existing deployment is detected and passed to resource modules."""

    @patch("three_stars.deploy._print_health_check")
    @patch("three_stars.deploy.storage")
    @patch("three_stars.deploy.agentcore")
    @patch("three_stars.deploy.edge")
    @patch("three_stars.deploy.cdn")
    @patch("three_stars.deploy.save_state")
    @patch("three_stars.deploy.load_state")
    @patch("three_stars.deploy.backup_state")
    @patch("three_stars.deploy.AWSContext")
    @patch("three_stars.deploy.compute_names")
    def test_deploy_update_detection(
        self,
        compute_names_mock,
        aws_ctx_mock,
        backup_mock,
        load_state_mock,
        save_state_mock,
        cdn_mock,
        edge_mock,
        agentcore_mock,
        storage_mock,
        health_mock,
        config,
        names,
    ):
        from three_stars.deploy import run_deploy

        compute_names_mock.return_value = names
        ctx_instance = MagicMock()
        ctx_instance.account_id = "123456789012"
        aws_ctx_mock.create.return_value = ctx_instance

        # Simulate an existing deployment
        existing_state = create_initial_state("test-app", "us-east-1")
        existing_agentcore = _agentcore_state()
        existing_state.agentcore = existing_agentcore
        existing_edge = _edge_state()
        existing_state.edge = existing_edge
        existing_cdn = _cdn_state()
        existing_state.cdn = existing_cdn
        existing_storage = _storage_state()
        existing_state.storage = existing_storage
        load_state_mock.return_value = existing_state

        _setup_mocks(storage_mock, agentcore_mock, edge_mock, cdn_mock)

        run_deploy(config)

        # agentcore.deploy should receive existing= with the previous state
        agentcore_deploy_kwargs = agentcore_mock.deploy.call_args.kwargs
        assert agentcore_deploy_kwargs.get("existing") == existing_agentcore

        # edge.deploy should receive existing= with the previous edge state
        edge_deploy_kwargs = edge_mock.deploy.call_args.kwargs
        assert edge_deploy_kwargs.get("existing") == existing_edge

        # cdn.deploy should receive existing= with the previous cdn state
        cdn_deploy_kwargs = cdn_mock.deploy.call_args.kwargs
        assert cdn_deploy_kwargs.get("existing") == existing_cdn

        # Cache invalidation should be called for updates
        cdn_mock.invalidate_cache.assert_called_once()


class TestDeployForceFlag:
    """Verify force=True causes resource modules to receive existing=None."""

    @patch("three_stars.deploy._print_health_check")
    @patch("three_stars.deploy.storage")
    @patch("three_stars.deploy.agentcore")
    @patch("three_stars.deploy.edge")
    @patch("three_stars.deploy.cdn")
    @patch("three_stars.deploy.save_state")
    @patch("three_stars.deploy.load_state")
    @patch("three_stars.deploy.backup_state")
    @patch("three_stars.deploy.AWSContext")
    @patch("three_stars.deploy.compute_names")
    def test_deploy_force_flag(
        self,
        compute_names_mock,
        aws_ctx_mock,
        backup_mock,
        load_state_mock,
        save_state_mock,
        cdn_mock,
        edge_mock,
        agentcore_mock,
        storage_mock,
        health_mock,
        config,
        names,
    ):
        from three_stars.deploy import run_deploy

        compute_names_mock.return_value = names
        ctx_instance = MagicMock()
        ctx_instance.account_id = "123456789012"
        aws_ctx_mock.create.return_value = ctx_instance

        # Simulate an existing deployment with all resources populated
        existing_state = create_initial_state("test-app", "us-east-1")
        existing_state.agentcore = _agentcore_state()
        existing_state.edge = _edge_state()
        existing_state.cdn = _cdn_state()
        existing_state.storage = _storage_state()
        load_state_mock.return_value = existing_state

        _setup_mocks(storage_mock, agentcore_mock, edge_mock, cdn_mock)

        run_deploy(config, force=True)

        # All resource deploy calls should receive existing=None
        agentcore_deploy_kwargs = agentcore_mock.deploy.call_args.kwargs
        assert agentcore_deploy_kwargs.get("existing") is None

        edge_deploy_kwargs = edge_mock.deploy.call_args.kwargs
        assert edge_deploy_kwargs.get("existing") is None

        cdn_deploy_kwargs = cdn_mock.deploy.call_args.kwargs
        assert cdn_deploy_kwargs.get("existing") is None
