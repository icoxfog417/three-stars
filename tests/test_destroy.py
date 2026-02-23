"""Orchestration tests for the destroy command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from three_stars.state import (
    AgentCoreState,
    CdnState,
    DeploymentState,
    EdgeState,
    StorageState,
)


def _full_state() -> DeploymentState:
    return DeploymentState(
        version=1,
        project_name="test-app",
        region="us-east-1",
        deployed_at="2026-01-01T00:00:00",
        agentcore=AgentCoreState(
            iam_role_name="test-role",
            iam_role_arn="arn:aws:iam::123:role/test",
            runtime_id="rt-123",
            runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/rt-123",
            endpoint_name="DEFAULT",
            endpoint_arn="arn:aws:bedrock-agentcore:us-east-1:123:endpoint/ep-123",
        ),
        storage=StorageState(s3_bucket="test-bucket"),
        edge=EdgeState(
            role_name="test-edge-role",
            role_arn="arn:aws:iam::123:role/edge",
            function_name="test-edge-fn",
            function_arn="arn:aws:lambda:us-east-1:123:function/edge:1",
        ),
        cdn=CdnState(
            distribution_id="E1234",
            domain="d1234.cloudfront.net",
            arn="arn:aws:cloudfront::123:distribution/E1234",
            oac_id="oac-123",
            lambda_oac_id="",
        ),
    )


@patch("three_stars.destroy.cdn")
@patch("three_stars.destroy.edge")
@patch("three_stars.destroy.agentcore")
@patch("three_stars.destroy.storage")
@patch("three_stars.destroy.AWSContext")
@patch("three_stars.destroy.save_state")
@patch("three_stars.destroy.delete_state")
@patch("three_stars.destroy.load_state")
def test_destroy_step_order(
    mock_load_state: MagicMock,
    mock_delete_state: MagicMock,
    mock_save_state: MagicMock,
    mock_aws_context: MagicMock,
    mock_storage: MagicMock,
    mock_agentcore: MagicMock,
    mock_edge: MagicMock,
    mock_cdn: MagicMock,
) -> None:
    """With all 4 resources, verify the destroy order:
    cdn.remove_edge_associations -> edge.destroy -> agentcore.destroy ->
    storage.destroy -> cdn.disable_and_delete_distribution.
    """
    from three_stars.destroy import run_destroy

    mock_load_state.return_value = _full_state()
    mock_edge.destroy.return_value = True

    call_order: list[str] = []
    mock_cdn.remove_edge_associations.side_effect = lambda *a, **kw: call_order.append(
        "cdn.remove_edge_associations"
    )
    mock_edge.destroy.side_effect = lambda *a, **kw: (
        call_order.append("edge.destroy"),
        True,
    )[1]
    mock_agentcore.destroy.side_effect = lambda *a, **kw: call_order.append("agentcore.destroy")
    mock_storage.destroy.side_effect = lambda *a, **kw: call_order.append("storage.destroy")
    mock_cdn.disable_and_delete_distribution.side_effect = lambda *a, **kw: call_order.append(
        "cdn.disable_and_delete_distribution"
    )

    run_destroy("/tmp/test-project", skip_confirm=True)

    assert call_order == [
        "cdn.remove_edge_associations",
        "edge.destroy",
        "agentcore.destroy",
        "storage.destroy",
        "cdn.disable_and_delete_distribution",
    ]


@patch("three_stars.destroy.cdn")
@patch("three_stars.destroy.edge")
@patch("three_stars.destroy.agentcore")
@patch("three_stars.destroy.storage")
@patch("three_stars.destroy.AWSContext")
@patch("three_stars.destroy.save_state")
@patch("three_stars.destroy.delete_state")
@patch("three_stars.destroy.load_state")
def test_destroy_state_cleanup(
    mock_load_state: MagicMock,
    mock_delete_state: MagicMock,
    mock_save_state: MagicMock,
    mock_aws_context: MagicMock,
    mock_storage: MagicMock,
    mock_agentcore: MagicMock,
    mock_edge: MagicMock,
    mock_cdn: MagicMock,
) -> None:
    """On successful destroy with no exceptions, delete_state is called."""
    from three_stars.destroy import run_destroy

    mock_load_state.return_value = _full_state()
    mock_edge.destroy.return_value = True

    run_destroy("/tmp/test-project", skip_confirm=True)

    mock_delete_state.assert_called_once_with("/tmp/test-project")
    mock_save_state.assert_not_called()


@patch("three_stars.destroy.cdn")
@patch("three_stars.destroy.edge")
@patch("three_stars.destroy.agentcore")
@patch("three_stars.destroy.storage")
@patch("three_stars.destroy.AWSContext")
@patch("three_stars.destroy.save_state")
@patch("three_stars.destroy.delete_state")
@patch("three_stars.destroy.load_state")
def test_destroy_partial_failure(
    mock_load_state: MagicMock,
    mock_delete_state: MagicMock,
    mock_save_state: MagicMock,
    mock_aws_context: MagicMock,
    mock_storage: MagicMock,
    mock_agentcore: MagicMock,
    mock_edge: MagicMock,
    mock_cdn: MagicMock,
) -> None:
    """When agentcore.destroy raises, storage and cdn are still destroyed.
    save_state is called (not delete_state) and the saved state preserves
    the agentcore that failed to delete.
    """
    from three_stars.destroy import run_destroy

    mock_load_state.return_value = _full_state()
    mock_edge.destroy.return_value = True
    mock_agentcore.destroy.side_effect = RuntimeError("AgentCore API error")

    run_destroy("/tmp/test-project", skip_confirm=True)

    # storage and cdn are still called despite agentcore failure
    mock_storage.destroy.assert_called_once()
    mock_cdn.disable_and_delete_distribution.assert_called_once()

    # save_state is called, not delete_state
    mock_delete_state.assert_not_called()
    mock_save_state.assert_called_once()

    # Inspect the saved state: agentcore should still be present (it failed),
    # but storage and cdn should be None (they succeeded).
    saved_state = mock_save_state.call_args[0][1]
    assert saved_state.agentcore is not None, "agentcore should be preserved (it failed)"
    assert saved_state.storage is None, "storage should be cleared (it succeeded)"
    assert saved_state.cdn is None, "cdn should be cleared (it succeeded)"
    assert saved_state.edge is None, "edge should be cleared (it succeeded)"
