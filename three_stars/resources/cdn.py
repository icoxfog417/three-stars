"""CDN resource module — CloudFront distribution + OACs + bucket policy."""

from __future__ import annotations

import time
import uuid
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError

from three_stars.config import ProjectConfig
from three_stars.naming import ResourceNames
from three_stars.resources import ResourceStatus
from three_stars.state import CdnState


def deploy(
    session: boto3.Session,
    config: ProjectConfig,
    names: ResourceNames,
    *,
    bucket_name: str,
    lambda_function_url: str,
    lambda_function_name: str,
    edge_function_arn: str,
    tags: dict[str, str] | None = None,
    existing: CdnState | None = None,
) -> CdnState:
    """Create CloudFront distribution + OACs.

    Args:
        bucket_name: S3 bucket for default origin.
        lambda_function_url: Lambda function URL for API origin.
        lambda_function_name: Lambda function name (for granting CloudFront access).
        edge_function_arn: Versioned Lambda@Edge ARN.
        tags: Dict format tags for CloudFront.
        existing: Existing state if updating (skips creation).
    """
    if existing:
        return existing

    prefix = names.prefix

    # Create OACs
    oac_id = _create_origin_access_control(session, f"{prefix}-oac")
    lambda_oac_id = _create_origin_access_control(
        session, f"{prefix}-lambda-oac", origin_type="lambda"
    )

    # Create distribution
    dist_info = _create_distribution(
        session,
        bucket_name=bucket_name,
        region=config.region,
        oac_id=oac_id,
        lambda_function_url=lambda_function_url,
        lambda_oac_id=lambda_oac_id,
        edge_function_arn=edge_function_arn,
        index_document=config.app.index,
        api_prefix=config.api.prefix,
        comment=f"three-stars: {config.name}",
        tags=tags,
    )

    # Set S3 bucket policy for CloudFront access
    from three_stars.resources.storage import set_bucket_policy_for_cloudfront

    set_bucket_policy_for_cloudfront(session, bucket_name, dist_info["arn"])

    # Grant CloudFront OAC permission to invoke Lambda
    from three_stars.resources.api_bridge import grant_cloudfront_access

    grant_cloudfront_access(session, lambda_function_name, dist_info["arn"])

    return CdnState(
        distribution_id=dist_info["distribution_id"],
        domain=dist_info["domain_name"],
        arn=dist_info["arn"],
        oac_id=oac_id,
        lambda_oac_id=lambda_oac_id,
    )


def destroy(session: boto3.Session, state: CdnState) -> None:
    """Delete CloudFront distribution and OACs."""
    import contextlib

    with contextlib.suppress(Exception):
        _delete_distribution(session, state.distribution_id)

    for oac_id in [state.oac_id, state.lambda_oac_id]:
        with contextlib.suppress(Exception):
            _delete_origin_access_control(session, oac_id)


def get_status(session: boto3.Session, state: CdnState) -> list[ResourceStatus]:
    """Return CloudFront distribution status."""
    dist_id = state.distribution_id
    try:
        result = _get_distribution(session, dist_id)
        status = result["status"]
        if status == "Deployed":
            return [ResourceStatus("CloudFront", dist_id, "[green]Deployed[/green]")]
        else:
            return [ResourceStatus("CloudFront", dist_id, f"[yellow]{status}[/yellow]")]
    except Exception:
        return [ResourceStatus("CloudFront", dist_id, "[red]Not Found[/red]")]


def _create_origin_access_control(
    session: boto3.Session,
    name: str,
    origin_type: str = "s3",
) -> str:
    """Create an Origin Access Control. Returns the OAC ID."""
    cf = session.client("cloudfront")
    try:
        resp = cf.create_origin_access_control(
            OriginAccessControlConfig={
                "Name": name,
                "Description": f"OAC for {name}",
                "SigningProtocol": "sigv4",
                "SigningBehavior": "always",
                "OriginAccessControlOriginType": origin_type,
            }
        )
        return resp["OriginAccessControl"]["Id"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "OriginAccessControlAlreadyExists":
            # Find existing OAC by name
            paginator = cf.get_paginator("list_origin_access_controls")
            for page in paginator.paginate():
                for item in page["OriginAccessControlList"].get("Items", []):
                    if item["Name"] == name:
                        return item["Id"]
            raise RuntimeError(f"OAC '{name}' exists but could not be found in listing") from None
        raise


def _create_distribution(
    session: boto3.Session,
    bucket_name: str,
    region: str,
    oac_id: str,
    lambda_function_url: str | None = None,
    lambda_oac_id: str | None = None,
    edge_function_arn: str | None = None,
    index_document: str = "index.html",
    api_prefix: str = "/api",
    comment: str = "",
    tags: dict[str, str] | None = None,
) -> dict:
    """Create a CloudFront distribution."""
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

    if lambda_function_url:
        parsed = urlparse(lambda_function_url)
        lambda_domain = parsed.hostname
        lambda_origin_id = "Lambda-API-Bridge"

        lambda_origin = {
            "Id": lambda_origin_id,
            "DomainName": lambda_domain,
            "CustomOriginConfig": {
                "HTTPPort": 80,
                "HTTPSPort": 443,
                "OriginProtocolPolicy": "https-only",
                "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
            },
        }
        if lambda_oac_id:
            lambda_origin["OriginAccessControlId"] = lambda_oac_id
        origins.append(lambda_origin)

        api_pattern = f"{api_prefix}/*" if not api_prefix.endswith("/*") else api_prefix
        # Use managed policies: CachingDisabled + AllViewerExceptHostHeader.
        # Forwarding the Host header breaks OAC SigV4 signing for Lambda URLs.
        _CACHE_POLICY_CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
        _ORP_ALL_VIEWER_EXCEPT_HOST = "b689b0a8-53d0-40ab-baf2-68738e2966ac"

        api_cache_behavior = {
            "PathPattern": api_pattern,
            "TargetOriginId": lambda_origin_id,
            "ViewerProtocolPolicy": "https-only",
            "AllowedMethods": {
                "Quantity": 7,
                "Items": ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"],
                "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
            },
            "Compress": True,
            "CachePolicyId": _CACHE_POLICY_CACHING_DISABLED,
            "OriginRequestPolicyId": _ORP_ALL_VIEWER_EXCEPT_HOST,
        }

        if edge_function_arn:
            api_cache_behavior["LambdaFunctionAssociations"] = {
                "Quantity": 1,
                "Items": [
                    {
                        "LambdaFunctionARN": edge_function_arn,
                        "EventType": "origin-request",
                        "IncludeBody": True,
                    }
                ],
            }

        cache_behaviors.append(api_cache_behavior)

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

    dist_config = {
        "CallerReference": caller_reference,
        "Comment": comment or "three-stars distribution",
        "Enabled": True,
        "DefaultRootObject": index_document,
        "Origins": {"Quantity": len(origins), "Items": origins},
        "DefaultCacheBehavior": default_cache_behavior,
        "ViewerCertificate": {"CloudFrontDefaultCertificate": True},
        "PriceClass": "PriceClass_100",
    }

    if cache_behaviors:
        dist_config["CacheBehaviors"] = {
            "Quantity": len(cache_behaviors),
            "Items": cache_behaviors,
        }

    if tags:
        tag_items = [{"Key": k, "Value": v} for k, v in tags.items()]
        resp = cf.create_distribution_with_tags(
            DistributionConfigWithTags={
                "DistributionConfig": dist_config,
                "Tags": {"Items": tag_items},
            }
        )
    else:
        resp = cf.create_distribution(DistributionConfig=dist_config)
    dist = resp["Distribution"]

    return {
        "distribution_id": dist["Id"],
        "domain_name": dist["DomainName"],
        "arn": dist["ARN"],
    }


def _get_distribution(session: boto3.Session, distribution_id: str) -> dict:
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


def invalidate_cache(
    session: boto3.Session,
    distribution_id: str,
    paths: list[str] | None = None,
) -> None:
    """Create a CloudFront cache invalidation.

    Args:
        distribution_id: CloudFront distribution ID.
        paths: List of paths to invalidate. Defaults to ``["/*"]``.
    """
    import contextlib

    cf = session.client("cloudfront")
    if paths is None:
        paths = ["/*"]
    with contextlib.suppress(ClientError):
        cf.create_invalidation(
            DistributionId=distribution_id,
            InvalidationBatch={
                "Paths": {"Quantity": len(paths), "Items": paths},
                "CallerReference": str(uuid.uuid4()),
            },
        )


def _delete_distribution(session: boto3.Session, distribution_id: str) -> None:
    """Disable and delete a CloudFront distribution."""
    cf = session.client("cloudfront")
    try:
        # Disable first
        resp = cf.get_distribution_config(Id=distribution_id)
        config = resp["DistributionConfig"]
        etag = resp["ETag"]

        if config["Enabled"]:
            config["Enabled"] = False
            cf.update_distribution(Id=distribution_id, DistributionConfig=config, IfMatch=etag)

        # Wait for deployed
        start = time.time()
        while time.time() - start < 600:
            resp = cf.get_distribution(Id=distribution_id)
            if resp["Distribution"]["Status"] == "Deployed":
                break
            time.sleep(15)

        # Delete
        resp = cf.get_distribution(Id=distribution_id)
        etag = resp["ETag"]
        cf.delete_distribution(Id=distribution_id, IfMatch=etag)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchDistribution":
            return
        raise


def _delete_origin_access_control(session: boto3.Session, oac_id: str) -> None:
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
