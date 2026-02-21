"""CloudFront distribution management."""

from __future__ import annotations

import time
import uuid
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError


def create_origin_access_control(
    session: boto3.Session,
    name: str,
) -> str:
    """Create an Origin Access Control for S3.

    Returns the OAC ID.
    """
    cf = session.client("cloudfront")
    resp = cf.create_origin_access_control(
        OriginAccessControlConfig={
            "Name": name,
            "Description": f"OAC for {name}",
            "SigningProtocol": "sigv4",
            "SigningBehavior": "always",
            "OriginAccessControlOriginType": "s3",
        }
    )
    return resp["OriginAccessControl"]["Id"]


def create_distribution(
    session: boto3.Session,
    bucket_name: str,
    region: str,
    oac_id: str,
    lambda_function_url: str | None = None,
    index_document: str = "index.html",
    api_prefix: str = "/api",
    comment: str = "",
) -> dict:
    """Create a CloudFront distribution with S3 origin and optional Lambda API origin.

    Args:
        session: boto3 session.
        bucket_name: S3 bucket name (origin).
        region: S3 bucket region.
        oac_id: Origin Access Control ID.
        lambda_function_url: Lambda function URL for API bridge (optional).
        index_document: Default root object.
        api_prefix: API path prefix for Lambda routing.
        comment: Distribution comment.

    Returns:
        Dict with 'distribution_id', 'domain_name', 'arn'.
    """
    cf = session.client("cloudfront")
    caller_reference = str(uuid.uuid4())
    s3_origin_id = f"S3-{bucket_name}"
    s3_domain = f"{bucket_name}.s3.{region}.amazonaws.com"

    origins = [
        {
            "Id": s3_origin_id,
            "DomainName": s3_domain,
            "OriginAccessControlId": oac_id,
            "S3OriginConfig": {"OriginAccessIdentity": ""},
        }
    ]

    cache_behaviors = []

    # Add Lambda function URL as second origin for API routing
    if lambda_function_url:
        parsed = urlparse(lambda_function_url)
        lambda_domain = parsed.hostname
        lambda_origin_id = "Lambda-API-Bridge"

        origins.append(
            {
                "Id": lambda_origin_id,
                "DomainName": lambda_domain,
                "CustomOriginConfig": {
                    "HTTPPort": 80,
                    "HTTPSPort": 443,
                    "OriginProtocolPolicy": "https-only",
                    "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
                },
            }
        )

        # Add cache behavior for /api/* that routes to Lambda
        api_pattern = f"{api_prefix}/*" if not api_prefix.endswith("/*") else api_prefix
        cache_behaviors.append(
            {
                "PathPattern": api_pattern,
                "TargetOriginId": lambda_origin_id,
                "ViewerProtocolPolicy": "https-only",
                "AllowedMethods": {
                    "Quantity": 7,
                    "Items": [
                        "GET",
                        "HEAD",
                        "OPTIONS",
                        "PUT",
                        "POST",
                        "PATCH",
                        "DELETE",
                    ],
                    "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
                },
                "Compress": True,
                "ForwardedValues": {
                    "QueryString": True,
                    "Cookies": {"Forward": "all"},
                    "Headers": {"Quantity": 1, "Items": ["*"]},
                },
                "MinTTL": 0,
                "DefaultTTL": 0,
                "MaxTTL": 0,
            }
        )

    # Default cache behavior (S3 frontend)
    default_cache_behavior = {
        "TargetOriginId": s3_origin_id,
        "ViewerProtocolPolicy": "redirect-to-https",
        "AllowedMethods": {
            "Quantity": 2,
            "Items": ["GET", "HEAD"],
            "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
        },
        "Compress": True,
        "ForwardedValues": {
            "QueryString": False,
            "Cookies": {"Forward": "none"},
        },
        "MinTTL": 0,
        "DefaultTTL": 86400,
        "MaxTTL": 31536000,
    }

    config = {
        "CallerReference": caller_reference,
        "Comment": comment or "three-stars distribution",
        "Enabled": True,
        "DefaultRootObject": index_document,
        "Origins": {
            "Quantity": len(origins),
            "Items": origins,
        },
        "DefaultCacheBehavior": default_cache_behavior,
        "ViewerCertificate": {
            "CloudFrontDefaultCertificate": True,
        },
        "PriceClass": "PriceClass_100",
    }

    if cache_behaviors:
        config["CacheBehaviors"] = {
            "Quantity": len(cache_behaviors),
            "Items": cache_behaviors,
        }

    resp = cf.create_distribution(DistributionConfig=config)
    dist = resp["Distribution"]

    return {
        "distribution_id": dist["Id"],
        "domain_name": dist["DomainName"],
        "arn": dist["ARN"],
    }


def get_distribution(session: boto3.Session, distribution_id: str) -> dict:
    """Get distribution details."""
    cf = session.client("cloudfront")
    resp = cf.get_distribution(Id=distribution_id)
    dist = resp["Distribution"]
    return {
        "distribution_id": dist["Id"],
        "domain_name": dist["DomainName"],
        "arn": dist["ARN"],
        "status": dist["Status"],
        "enabled": dist["DistributionConfig"]["Enabled"],
        "etag": resp["ETag"],
    }


def disable_distribution(session: boto3.Session, distribution_id: str) -> None:
    """Disable a CloudFront distribution (required before deletion)."""
    cf = session.client("cloudfront")
    resp = cf.get_distribution_config(Id=distribution_id)
    config = resp["DistributionConfig"]
    etag = resp["ETag"]

    if not config["Enabled"]:
        return

    config["Enabled"] = False
    cf.update_distribution(Id=distribution_id, DistributionConfig=config, IfMatch=etag)


def wait_for_distribution_deployed(
    session: boto3.Session,
    distribution_id: str,
    max_wait_seconds: int = 600,
) -> None:
    """Wait for distribution to reach 'Deployed' status."""
    cf = session.client("cloudfront")
    start = time.time()
    while time.time() - start < max_wait_seconds:
        resp = cf.get_distribution(Id=distribution_id)
        status = resp["Distribution"]["Status"]
        if status == "Deployed":
            return
        time.sleep(15)
    raise TimeoutError(
        f"Distribution {distribution_id} did not reach 'Deployed' status within {max_wait_seconds}s"
    )


def delete_distribution(session: boto3.Session, distribution_id: str) -> None:
    """Disable and delete a CloudFront distribution."""
    cf = session.client("cloudfront")
    try:
        # Disable first
        disable_distribution(session, distribution_id)

        # Wait for disabled state
        wait_for_distribution_deployed(session, distribution_id, max_wait_seconds=600)

        # Delete
        resp = cf.get_distribution(Id=distribution_id)
        etag = resp["ETag"]
        cf.delete_distribution(Id=distribution_id, IfMatch=etag)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchDistribution":
            return
        raise


def delete_origin_access_control(session: boto3.Session, oac_id: str) -> None:
    """Delete an Origin Access Control."""
    cf = session.client("cloudfront")
    try:
        resp = cf.get_origin_access_control(Id=oac_id)
        etag = resp["ETag"]
        cf.delete_origin_access_control(Id=oac_id, IfMatch=etag)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchOriginAccessControl":
            return
        raise
