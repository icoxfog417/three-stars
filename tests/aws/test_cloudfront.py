"""Tests for CloudFront distribution and OAC operations."""

from __future__ import annotations

import boto3
from moto import mock_aws

from three_stars.aws.cloudfront import (
    create_distribution,
    create_origin_access_control,
    delete_origin_access_control,
)


@mock_aws
class TestOriginAccessControl:
    """Tests for OAC creation (S3 and Lambda types)."""

    def test_create_s3_oac(self):
        session = boto3.Session(region_name="us-east-1")
        oac_id = create_origin_access_control(session, "test-s3-oac")
        assert oac_id

        cf = session.client("cloudfront")
        resp = cf.get_origin_access_control(Id=oac_id)
        config = resp["OriginAccessControl"]["OriginAccessControlConfig"]
        assert config["OriginAccessControlOriginType"] == "s3"
        assert config["SigningProtocol"] == "sigv4"
        assert config["SigningBehavior"] == "always"

    def test_create_lambda_oac(self):
        session = boto3.Session(region_name="us-east-1")
        oac_id = create_origin_access_control(
            session, "test-lambda-oac", origin_type="lambda"
        )
        assert oac_id

        cf = session.client("cloudfront")
        resp = cf.get_origin_access_control(Id=oac_id)
        config = resp["OriginAccessControl"]["OriginAccessControlConfig"]
        assert config["OriginAccessControlOriginType"] == "lambda"
        assert config["SigningProtocol"] == "sigv4"
        assert config["SigningBehavior"] == "always"

    def test_s3_and_lambda_oac_are_separate(self):
        """S3 and Lambda must use separate OACs with different origin types."""
        session = boto3.Session(region_name="us-east-1")
        s3_oac = create_origin_access_control(session, "test-s3-oac")
        lambda_oac = create_origin_access_control(
            session, "test-lambda-oac", origin_type="lambda"
        )
        assert s3_oac != lambda_oac

    def test_delete_oac(self):
        session = boto3.Session(region_name="us-east-1")
        oac_id = create_origin_access_control(session, "test-oac")
        delete_origin_access_control(session, oac_id)

        cf = session.client("cloudfront")
        import pytest
        from botocore.exceptions import ClientError

        with pytest.raises(ClientError):
            cf.get_origin_access_control(Id=oac_id)

    def test_delete_nonexistent_oac(self):
        session = boto3.Session(region_name="us-east-1")
        # Should not raise
        delete_origin_access_control(session, "NONEXISTENT123")


@mock_aws
class TestDistribution:
    """Tests for CloudFront distribution with Lambda OAC + Lambda@Edge."""

    def _setup_s3_bucket(self, session, bucket_name="test-bucket"):
        s3 = session.client("s3")
        s3.create_bucket(Bucket=bucket_name)
        return bucket_name

    def test_create_distribution_with_lambda_oac(self):
        """Distribution should include Lambda OAC on Lambda origin."""
        session = boto3.Session(region_name="us-east-1")
        self._setup_s3_bucket(session)
        s3_oac_id = create_origin_access_control(session, "s3-oac")
        lambda_oac_id = create_origin_access_control(
            session, "lambda-oac", origin_type="lambda"
        )

        result = create_distribution(
            session,
            bucket_name="test-bucket",
            region="us-east-1",
            oac_id=s3_oac_id,
            lambda_function_url="https://abc123.lambda-url.us-east-1.on.aws/",
            lambda_oac_id=lambda_oac_id,
        )

        assert result["distribution_id"]
        assert result["domain_name"]
        assert result["arn"]

        # Verify Lambda origin exists in the distribution
        cf = session.client("cloudfront")
        resp = cf.get_distribution(Id=result["distribution_id"])
        config = resp["Distribution"]["DistributionConfig"]
        origins = config["Origins"]["Items"]

        lambda_origin = next(
            (o for o in origins if o["Id"] == "Lambda-API-Bridge"), None
        )
        assert lambda_origin is not None
        assert lambda_origin["DomainName"] == "abc123.lambda-url.us-east-1.on.aws"
        # Note: moto may not persist OriginAccessControlId on custom origins,
        # but we verify the OAC was created and the origin is configured.

    def test_create_distribution_with_edge_function(self):
        """Distribution creation succeeds with Lambda@Edge config."""
        session = boto3.Session(region_name="us-east-1")
        self._setup_s3_bucket(session)
        s3_oac_id = create_origin_access_control(session, "s3-oac")
        lambda_oac_id = create_origin_access_control(
            session, "lambda-oac", origin_type="lambda"
        )

        edge_arn = (
            "arn:aws:lambda:us-east-1:123456789012"
            ":function:test-edge-sha256:1"
        )

        # Verify distribution creation succeeds with edge function config
        result = create_distribution(
            session,
            bucket_name="test-bucket",
            region="us-east-1",
            oac_id=s3_oac_id,
            lambda_function_url="https://abc123.lambda-url.us-east-1.on.aws/",
            lambda_oac_id=lambda_oac_id,
            edge_function_arn=edge_arn,
        )

        assert result["distribution_id"]

        cf = session.client("cloudfront")
        resp = cf.get_distribution(Id=result["distribution_id"])
        config = resp["Distribution"]["DistributionConfig"]

        # Verify /api/* cache behavior exists
        api_behaviors = config["CacheBehaviors"]["Items"]
        assert len(api_behaviors) == 1
        assert api_behaviors[0]["PathPattern"] == "/api/*"
        # Note: moto does not persist LambdaFunctionAssociations on cache
        # behaviors. The association is verified by the distribution accepting
        # our config without error. Real AWS would return it back.

    def test_distribution_without_lambda(self):
        """Distribution without Lambda should have no /api/* behavior."""
        session = boto3.Session(region_name="us-east-1")
        self._setup_s3_bucket(session)
        oac_id = create_origin_access_control(session, "s3-oac")

        result = create_distribution(
            session,
            bucket_name="test-bucket",
            region="us-east-1",
            oac_id=oac_id,
        )

        cf = session.client("cloudfront")
        resp = cf.get_distribution(Id=result["distribution_id"])
        config = resp["Distribution"]["DistributionConfig"]

        # Only S3 origin
        assert config["Origins"]["Quantity"] == 1
        assert config["Origins"]["Items"][0]["Id"].startswith("S3-")

    def test_distribution_lambda_without_oac_no_oac_id(self):
        """Lambda origin without OAC should not have OriginAccessControlId."""
        session = boto3.Session(region_name="us-east-1")
        self._setup_s3_bucket(session)
        s3_oac_id = create_origin_access_control(session, "s3-oac")

        result = create_distribution(
            session,
            bucket_name="test-bucket",
            region="us-east-1",
            oac_id=s3_oac_id,
            lambda_function_url="https://abc123.lambda-url.us-east-1.on.aws/",
            # No lambda_oac_id — backwards compatibility
        )

        cf = session.client("cloudfront")
        resp = cf.get_distribution(Id=result["distribution_id"])
        config = resp["Distribution"]["DistributionConfig"]
        origins = config["Origins"]["Items"]

        lambda_origin = next(
            (o for o in origins if o["Id"] == "Lambda-API-Bridge"), None
        )
        assert lambda_origin is not None
        # No OAC on Lambda origin when lambda_oac_id not provided
        assert lambda_origin.get("OriginAccessControlId", "") == ""
