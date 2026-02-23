"""Contract tests for the edge resource module (Lambda@Edge)."""

from __future__ import annotations

from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from three_stars.naming import ResourceNames
from three_stars.resources import ResourceStatus, edge
from three_stars.resources._base import AWSContext
from three_stars.state import EdgeState

RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/test-runtime"
REGION = "us-east-1"


def _make_names() -> ResourceNames:
    return ResourceNames(
        prefix="sss-test",
        bucket="sss-test-abc12345",
        agentcore_role="sss-test-role",
        agent_name="sss_test_agent",
        endpoint_name="sss_test_endpoint",
        lambda_role="sss-test-lambda-role",
        lambda_function="sss-test-api-bridge",
        edge_role="sss-test-edge-role",
        edge_function="sss-test-edge-sha256",
        memory="sss_test_memory",
    )


def _make_ctx() -> AWSContext:
    session = boto3.Session(region_name="us-east-1")
    return AWSContext(session)


@mock_aws
@patch("time.sleep")
class TestEdge:
    """Contract tests for edge.deploy / destroy / get_status."""

    # ---- deploy ----

    def test_deploy_returns_state(self, _sleep):
        """deploy() creates IAM role + Lambda function and returns EdgeState."""
        ctx = _make_ctx()
        names = _make_names()

        state = edge.deploy(ctx, names, runtime_arn=RUNTIME_ARN, region=REGION)

        assert isinstance(state, EdgeState)
        assert state.role_name == names.edge_role
        assert state.role_arn  # non-empty ARN string
        assert "arn:aws:iam::" in state.role_arn
        assert state.function_name == names.edge_function
        assert state.function_arn  # non-empty versioned ARN
        assert ":function:" in state.function_arn

        # Verify the IAM role actually exists
        iam = ctx.client("iam")
        role = iam.get_role(RoleName=names.edge_role)
        assert role["Role"]["Arn"] == state.role_arn

        # Verify the Lambda function actually exists
        lam = ctx.client("lambda", region_name="us-east-1")
        fn = lam.get_function(FunctionName=names.edge_function)
        assert fn["Configuration"]["State"] == "Active"

    def test_deploy_update_idempotent(self, _sleep):
        """Passing existing= skips creation and returns the same state."""
        ctx = _make_ctx()
        names = _make_names()

        # First deploy to create resources
        state = edge.deploy(ctx, names, runtime_arn=RUNTIME_ARN, region=REGION)

        # Second deploy with existing — should update code and return same state
        updated = edge.deploy(ctx, names, runtime_arn=RUNTIME_ARN, region=REGION, existing=state)

        assert updated is state  # exact same object returned
        assert updated.role_name == state.role_name
        assert updated.function_arn == state.function_arn

        # Function should still exist and be active
        lam = ctx.client("lambda", region_name="us-east-1")
        fn = lam.get_function(FunctionName=names.edge_function)
        assert fn["Configuration"]["State"] == "Active"

    def test_deploy_error_handling(self, _sleep):
        """IAM permission errors surface as ClientError."""
        ctx = _make_ctx()
        names = _make_names()

        error = ClientError(
            {
                "Error": {
                    "Code": "AccessDenied",
                    "Message": "User is not authorized",
                }
            },
            "CreateRole",
        )

        with patch("three_stars.resources.edge._create_edge_role", side_effect=error):
            with pytest.raises(ClientError) as exc_info:
                edge.deploy(ctx, names, runtime_arn=RUNTIME_ARN, region=REGION)

            assert exc_info.value.response["Error"]["Code"] == "AccessDenied"

    # ---- destroy ----

    def test_destroy_cleans_up(self, _sleep):
        """destroy() deletes function and role."""
        ctx = _make_ctx()
        names = _make_names()

        state = edge.deploy(ctx, names, runtime_arn=RUNTIME_ARN, region=REGION)

        result = edge.destroy(ctx, state)
        assert result is True

        # Lambda function should be gone
        lam = ctx.client("lambda", region_name="us-east-1")
        with pytest.raises(ClientError) as exc_info:
            lam.get_function(FunctionName=names.edge_function)
        assert exc_info.value.response["Error"]["Code"] == "ResourceNotFoundException"

        # IAM role should be gone
        iam = ctx.client("iam")
        with pytest.raises(ClientError) as exc_info:
            iam.get_role(RoleName=names.edge_role)
        assert exc_info.value.response["Error"]["Code"] == "NoSuchEntity"

    def test_destroy_idempotent(self, _sleep):
        """Destroying already-deleted resources is a no-op (returns True)."""
        ctx = _make_ctx()
        names = _make_names()

        state = edge.deploy(ctx, names, runtime_arn=RUNTIME_ARN, region=REGION)

        # First destroy — should succeed
        result1 = edge.destroy(ctx, state)
        assert result1 is True

        # Second destroy — resources already gone, should still return True
        result2 = edge.destroy(ctx, state)
        assert result2 is True

    # ---- get_status ----

    def test_get_status(self, _sleep):
        """get_status returns Active for existing function, Not Found for deleted."""
        ctx = _make_ctx()
        names = _make_names()

        state = edge.deploy(ctx, names, runtime_arn=RUNTIME_ARN, region=REGION)

        # Status of existing function
        statuses = edge.get_status(ctx, state)
        assert len(statuses) == 1
        assert isinstance(statuses[0], ResourceStatus)
        assert statuses[0].resource == "Lambda@Edge"
        assert statuses[0].id == names.edge_function
        assert "Active" in statuses[0].status

        # Destroy and check again
        edge.destroy(ctx, state)
        statuses = edge.get_status(ctx, state)
        assert len(statuses) == 1
        assert "Not Found" in statuses[0].status
