"""Tests for API bridge resource module (Lambda + IAM role + function URL)."""

from __future__ import annotations

import json
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from three_stars.resources.api_bridge import (
    _create_lambda_function,
    _create_lambda_role,
    _delete_lambda_function,
    _delete_lambda_role,
    grant_cloudfront_access,
)


def _create_role(session, role_name="test-lambda-role"):
    """Helper: create a Lambda execution role for tests."""
    iam = session.client("iam")
    resp = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "lambda.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
        ),
    )
    return resp["Role"]["Arn"]


@mock_aws
class TestLambdaFunction:
    """Tests for Lambda bridge function creation and function URL auth."""

    def test_create_function_returns_url(self):
        session = boto3.Session(region_name="us-east-1")
        role_arn = _create_role(session)

        result = _create_lambda_function(
            session,
            function_name="test-bridge",
            role_arn=role_arn,
            agent_runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/test",
            endpoint_name="test_endpoint",
            region="us-east-1",
        )

        assert result["function_name"] == "test-bridge"
        assert result["function_arn"]
        assert result["function_url"].startswith("https://")

    def test_function_url_uses_iam_auth(self):
        """Function URL must use AWS_IAM auth, not NONE."""
        session = boto3.Session(region_name="us-east-1")
        role_arn = _create_role(session)

        _create_lambda_function(
            session,
            function_name="test-bridge",
            role_arn=role_arn,
            agent_runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/test",
            endpoint_name="test_endpoint",
            region="us-east-1",
        )

        lam = session.client("lambda")
        url_config = lam.get_function_url_config(FunctionName="test-bridge")
        assert url_config["AuthType"] == "AWS_IAM"

    def test_create_function_idempotent(self):
        session = boto3.Session(region_name="us-east-1")
        role_arn = _create_role(session)
        kwargs = dict(
            function_name="test-bridge",
            role_arn=role_arn,
            agent_runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/test",
            endpoint_name="test_endpoint",
            region="us-east-1",
        )

        result1 = _create_lambda_function(session, **kwargs)
        result2 = _create_lambda_function(session, **kwargs)
        assert result1["function_url"] == result2["function_url"]


@mock_aws
class TestGrantCloudfrontAccess:
    """Tests for CloudFront OAC permission on Lambda."""

    def test_adds_cloudfront_permission(self):
        session = boto3.Session(region_name="us-east-1")
        role_arn = _create_role(session)

        _create_lambda_function(
            session,
            function_name="test-bridge",
            role_arn=role_arn,
            agent_runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/test",
            endpoint_name="test_endpoint",
            region="us-east-1",
        )

        dist_arn = "arn:aws:cloudfront::123456789012:distribution/EDFDVBD6EXAMPLE"
        grant_cloudfront_access(session, "test-bridge", dist_arn)

        lam = session.client("lambda")
        policy_resp = lam.get_policy(FunctionName="test-bridge")
        policy = json.loads(policy_resp["Policy"])

        cf_stmts = [
            s
            for s in policy["Statement"]
            if s.get("Principal", {}).get("Service") == "cloudfront.amazonaws.com"
        ]
        assert len(cf_stmts) == 2
        actions = {s["Action"] for s in cf_stmts}
        assert actions == {"lambda:InvokeFunctionUrl", "lambda:InvokeFunction"}

    def test_idempotent(self):
        session = boto3.Session(region_name="us-east-1")
        role_arn = _create_role(session)

        _create_lambda_function(
            session,
            function_name="test-bridge",
            role_arn=role_arn,
            agent_runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/test",
            endpoint_name="test_endpoint",
            region="us-east-1",
        )

        dist_arn = "arn:aws:cloudfront::123456789012:distribution/EDFDVBD6EXAMPLE"
        grant_cloudfront_access(session, "test-bridge", dist_arn)
        grant_cloudfront_access(session, "test-bridge", dist_arn)


@mock_aws
class TestEdgeRole:
    """Tests for Lambda@Edge IAM role (via api_bridge for backward compat)."""

    @patch("three_stars.resources.edge.time.sleep")
    def test_create_edge_role(self, mock_sleep):
        from three_stars.resources.edge import _create_edge_role

        session = boto3.Session(region_name="us-east-1")
        arn = _create_edge_role(session, "test-edge-role")
        assert "test-edge-role" in arn

        iam = session.client("iam")
        role = iam.get_role(RoleName="test-edge-role")
        trust = role["Role"]["AssumeRolePolicyDocument"]
        principals = trust["Statement"][0]["Principal"]["Service"]
        assert "lambda.amazonaws.com" in principals
        assert "edgelambda.amazonaws.com" in principals

        policies = iam.list_role_policies(RoleName="test-edge-role")
        assert "lambda-edge-basic-execution" in policies["PolicyNames"]

    @patch("three_stars.resources.edge.time.sleep")
    def test_delete_edge_role(self, mock_sleep):
        from three_stars.resources.edge import _create_edge_role, _delete_edge_role

        session = boto3.Session(region_name="us-east-1")
        _create_edge_role(session, "test-edge-role")

        _delete_edge_role(session, "test-edge-role")

        iam = session.client("iam")
        with pytest.raises(iam.exceptions.NoSuchEntityException):
            iam.get_role(RoleName="test-edge-role")


@mock_aws
class TestEdgeFunction:
    """Tests for Lambda@Edge SHA256 function."""

    def _create_edge_role_arn(self, session):
        iam = session.client("iam")
        resp = iam.create_role(
            RoleName="test-edge-role",
            AssumeRolePolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {
                                "Service": [
                                    "lambda.amazonaws.com",
                                    "edgelambda.amazonaws.com",
                                ]
                            },
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
        )
        return resp["Role"]["Arn"]

    def test_create_edge_function_returns_versioned_arn(self):
        from three_stars.resources.edge import _create_edge_function

        session = boto3.Session(region_name="us-east-1")
        role_arn = self._create_edge_role_arn(session)

        versioned_arn = _create_edge_function(session, "test-edge-sha256", role_arn)

        assert ":test-edge-sha256:" in versioned_arn
        version_part = versioned_arn.split(":")[-1]
        assert version_part.isdigit(), f"Expected version number, got {version_part}"

    def test_edge_function_created_in_us_east_1(self):
        """Lambda@Edge must be in us-east-1 regardless of session region."""
        from three_stars.resources.edge import _create_edge_function

        session = boto3.Session(region_name="eu-west-1")
        role_arn = self._create_edge_role_arn(session)

        _create_edge_function(session, "test-edge-sha256", role_arn)

        lam = session.client("lambda", region_name="us-east-1")
        resp = lam.get_function(FunctionName="test-edge-sha256")
        assert resp["Configuration"]["Runtime"] == "nodejs20.x"


@mock_aws
class TestDeleteLambdaBridge:
    """Tests for Lambda bridge deletion."""

    def test_delete_function(self):
        session = boto3.Session(region_name="us-east-1")
        role_arn = _create_role(session)
        _create_lambda_function(
            session,
            function_name="test-bridge",
            role_arn=role_arn,
            agent_runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/test",
            endpoint_name="test_endpoint",
            region="us-east-1",
        )
        _delete_lambda_function(session, "test-bridge")

        lam = session.client("lambda")
        with pytest.raises(ClientError, match="ResourceNotFoundException"):
            lam.get_function(FunctionName="test-bridge")

    @patch("three_stars.resources.api_bridge.time.sleep")
    def test_delete_role(self, mock_sleep):
        session = boto3.Session(region_name="us-east-1")
        _create_lambda_role(session, "test-role", "123456789012", "us-east-1")
        _delete_lambda_role(session, "test-role")

        iam = session.client("iam")
        with pytest.raises(iam.exceptions.NoSuchEntityException):
            iam.get_role(RoleName="test-role")
