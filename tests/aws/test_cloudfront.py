"""Tests for CloudFront distribution and OAC operations."""

from __future__ import annotations

import boto3
import pytest
from botocore.exceptions import ClientError
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
        oac_id = create_origin_access_control(session, "test-lambda-oac", origin_type="lambda")
        assert oac_id

        cf = session.client("cloudfront")
        resp = cf.get_origin_access_control(Id=oac_id)
        config = resp["OriginAccessControl"]["OriginAccessControlConfig"]
        assert config["OriginAccessControlOriginType"] == "lambda"
        assert config["SigningProtocol"] == "sigv4"
        assert config["SigningBehavior"] == "always"

    def test_delete_oac(self):
        session = boto3.Session(region_name="us-east-1")
        oac_id = create_origin_access_control(session, "test-oac")
        delete_origin_access_control(session, oac_id)

        cf = session.client("cloudfront")
        with pytest.raises(ClientError):
            cf.get_origin_access_control(Id=oac_id)


@mock_aws
class TestDistribution:
    """Tests for CloudFront distribution with Lambda OAC."""

    def _setup_s3_bucket(self, session, bucket_name="test-bucket"):
        s3 = session.client("s3")
        s3.create_bucket(Bucket=bucket_name)
        return bucket_name

    def test_create_distribution_with_lambda_oac(self):
        """Distribution should include Lambda origin with OAC."""
        session = boto3.Session(region_name="us-east-1")
        self._setup_s3_bucket(session)
        s3_oac_id = create_origin_access_control(session, "s3-oac")
        lambda_oac_id = create_origin_access_control(session, "lambda-oac", origin_type="lambda")

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

        cf = session.client("cloudfront")
        resp = cf.get_distribution(Id=result["distribution_id"])
        origins = resp["Distribution"]["DistributionConfig"]["Origins"]["Items"]

        lambda_origin = next((o for o in origins if o["Id"] == "Lambda-API-Bridge"), None)
        assert lambda_origin is not None
        assert lambda_origin["DomainName"] == "abc123.lambda-url.us-east-1.on.aws"

    def test_distribution_without_lambda(self):
        """Distribution without Lambda should have only S3 origin."""
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

        assert config["Origins"]["Quantity"] == 1
        assert config["Origins"]["Items"][0]["Id"].startswith("S3-")
