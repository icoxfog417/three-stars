"""Tests for CDN resource module (CloudFront distribution + OAC)."""

from __future__ import annotations

from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from three_stars.config import ProjectConfig
from three_stars.naming import ResourceNames
from three_stars.resources import cdn
from three_stars.resources._base import AWSContext
from three_stars.resources.cdn import (
    _create_distribution,
    _create_origin_access_control,
    _delete_origin_access_control,
)
from three_stars.state import CdnState


def _make_ctx(region="us-east-1"):
    return AWSContext(boto3.Session(region_name=region))


def _make_names():
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


def _make_config(tmp_path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "agent.py").write_text("pass")
    (agent_dir / "requirements.txt").write_text("")
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "index.html").write_text("<html></html>")
    return ProjectConfig(name="test-app", region="us-east-1", project_dir=tmp_path)


@mock_aws
class TestOriginAccessControl:
    """Tests for OAC creation (S3 type only — Lambda OAC removed)."""

    def test_create_s3_oac(self):
        ctx = _make_ctx()
        oac_id = _create_origin_access_control(ctx, "test-s3-oac")
        assert oac_id

        cf = ctx.client("cloudfront")
        resp = cf.get_origin_access_control(Id=oac_id)
        config = resp["OriginAccessControl"]["OriginAccessControlConfig"]
        assert config["OriginAccessControlOriginType"] == "s3"
        assert config["SigningProtocol"] == "sigv4"
        assert config["SigningBehavior"] == "always"

    def test_delete_oac(self):
        ctx = _make_ctx()
        oac_id = _create_origin_access_control(ctx, "test-oac")
        _delete_origin_access_control(ctx, oac_id)

        cf = ctx.client("cloudfront")
        with pytest.raises(ClientError):
            cf.get_origin_access_control(Id=oac_id)


@mock_aws
class TestDistribution:
    """Tests for CloudFront distribution with AgentCore origin."""

    def _setup_s3_bucket(self, ctx, bucket_name="test-bucket"):
        s3 = ctx.client("s3")
        s3.create_bucket(Bucket=bucket_name)
        return bucket_name

    def test_create_distribution_with_agentcore_origin(self):
        """Distribution should include AgentCore custom HTTPS origin."""
        ctx = _make_ctx()
        self._setup_s3_bucket(ctx)
        s3_oac_id = _create_origin_access_control(ctx, "s3-oac")

        result = _create_distribution(
            ctx,
            bucket_name="test-bucket",
            region="us-east-1",
            oac_id=s3_oac_id,
            agentcore_region="us-east-1",
        )

        assert result["distribution_id"]
        assert result["domain_name"]
        assert result["arn"]

        cf = ctx.client("cloudfront")
        resp = cf.get_distribution(Id=result["distribution_id"])
        origins = resp["Distribution"]["DistributionConfig"]["Origins"]["Items"]

        ac_origin = next((o for o in origins if o["Id"] == "AgentCore-API"), None)
        assert ac_origin is not None
        assert ac_origin["DomainName"] == "bedrock-agentcore.us-east-1.amazonaws.com"

    def test_distribution_without_agentcore(self):
        """Distribution without AgentCore should have only S3 origin."""
        ctx = _make_ctx()
        self._setup_s3_bucket(ctx)
        oac_id = _create_origin_access_control(ctx, "s3-oac")

        result = _create_distribution(
            ctx,
            bucket_name="test-bucket",
            region="us-east-1",
            oac_id=oac_id,
        )

        cf = ctx.client("cloudfront")
        resp = cf.get_distribution(Id=result["distribution_id"])
        config = resp["Distribution"]["DistributionConfig"]

        assert config["Origins"]["Quantity"] == 1
        assert config["Origins"]["Items"][0]["Id"].startswith("S3-")


@mock_aws
class TestCdnContract:
    """Contract tests for deploy/destroy/get_status."""

    def _setup_s3_bucket(self, ctx, bucket_name="sss-test-abc12345"):
        s3 = ctx.client("s3")
        s3.create_bucket(Bucket=bucket_name)
        return bucket_name

    @patch("three_stars.resources.storage.set_bucket_policy_for_cloudfront")
    def test_deploy_returns_state(self, mock_policy, tmp_path):
        ctx = _make_ctx()
        self._setup_s3_bucket(ctx)
        config = _make_config(tmp_path)
        names = _make_names()

        state = cdn.deploy(
            ctx,
            config,
            names,
            bucket_name="sss-test-abc12345",
            agentcore_region="us-east-1",
            edge_function_arn="arn:aws:lambda:us-east-1:123:function/edge:1",
        )

        assert isinstance(state, CdnState)
        assert state.distribution_id
        assert state.domain
        assert state.arn
        assert state.oac_id

    @patch("three_stars.resources.storage.set_bucket_policy_for_cloudfront")
    def test_deploy_update_idempotent(self, mock_policy, tmp_path):
        ctx = _make_ctx()
        self._setup_s3_bucket(ctx)
        config = _make_config(tmp_path)
        names = _make_names()

        state = cdn.deploy(
            ctx,
            config,
            names,
            bucket_name="sss-test-abc12345",
            agentcore_region="us-east-1",
            edge_function_arn="arn:aws:lambda:us-east-1:123:function/edge:1",
        )

        # Second deploy with existing state should return same state
        state2 = cdn.deploy(
            ctx,
            config,
            names,
            bucket_name="sss-test-abc12345",
            agentcore_region="us-east-1",
            edge_function_arn="arn:aws:lambda:us-east-1:123:function/edge:1",
            existing=state,
        )

        assert state2 is state

    @patch("three_stars.resources.storage.set_bucket_policy_for_cloudfront")
    def test_get_status(self, mock_policy, tmp_path):
        ctx = _make_ctx()
        self._setup_s3_bucket(ctx)
        config = _make_config(tmp_path)
        names = _make_names()

        state = cdn.deploy(
            ctx,
            config,
            names,
            bucket_name="sss-test-abc12345",
            agentcore_region="us-east-1",
            edge_function_arn="arn:aws:lambda:us-east-1:123:function/edge:1",
        )

        rows = cdn.get_status(ctx, state)
        assert len(rows) == 1
        assert rows[0].resource == "CloudFront"
        # Moto returns "Deployed" status for new distributions
        assert "Deployed" in rows[0].status or "InProgress" in rows[0].status

    def test_get_status_not_found(self):
        ctx = _make_ctx()
        state = CdnState(
            distribution_id="E_NONEXISTENT",
            domain="d1234.cloudfront.net",
            arn="arn:aws:cloudfront::123:distribution/E_NONEXISTENT",
            oac_id="oac-123",
            lambda_oac_id="",
        )

        rows = cdn.get_status(ctx, state)
        assert "Not Found" in rows[0].status
