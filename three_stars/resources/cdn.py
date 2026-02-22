"""CDN resource module — CloudFront distribution + OAC + bucket policy."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from botocore.exceptions import ClientError

from three_stars.config import ProjectConfig
from three_stars.naming import ResourceNames
from three_stars.resources import ResourceStatus
from three_stars.resources._base import AWSContext
from three_stars.state import CdnState


def deploy(
    ctx: AWSContext,
    config: ProjectConfig,
    names: ResourceNames,
    *,
    bucket_name: str,
    agentcore_region: str,
    edge_function_arn: str,
    tags: dict[str, str] | None = None,
    existing: CdnState | None = None,
) -> CdnState:
    """Create CloudFront distribution + S3 OAC.

    The API origin points directly at AgentCore.  Lambda@Edge handles SigV4
    signing on origin-request, so no Lambda OAC is needed.

    Args:
        bucket_name: S3 bucket for default origin.
        agentcore_region: AWS region where AgentCore is deployed.
        edge_function_arn: Versioned Lambda@Edge ARN.
        tags: Dict format tags for CloudFront.
        existing: Existing state if updating (skips creation).
    """
    if existing:
        return existing

    prefix = names.prefix

    # Create S3 OAC only — no Lambda OAC needed (SigV4 is in Lambda@Edge)
    oac_id = _create_origin_access_control(ctx, f"{prefix}-oac")

    # Create distribution with AgentCore custom origin
    dist_info = _create_distribution(
        ctx,
        bucket_name=bucket_name,
        region=config.region,
        oac_id=oac_id,
        agentcore_region=agentcore_region,
        edge_function_arn=edge_function_arn,
        index_document=config.app.index,
        api_prefix=config.api.prefix,
        comment=f"three-stars: {config.name}",
        tags=tags,
    )

    # Set S3 bucket policy for CloudFront access
    from three_stars.resources.storage import set_bucket_policy_for_cloudfront

    set_bucket_policy_for_cloudfront(ctx, bucket_name, dist_info["arn"])

    return CdnState(
        distribution_id=dist_info["distribution_id"],
        domain=dist_info["domain_name"],
        arn=dist_info["arn"],
        oac_id=oac_id,
        lambda_oac_id="",  # No longer created; kept for state backward compat
    )


def remove_edge_associations(ctx: AWSContext, distribution_id: str) -> None:
    """Remove Lambda@Edge function associations from all cache behaviors.

    Waits until the distribution reaches ``Deployed`` status so that
    Lambda@Edge replicas start cleaning up and the function can be deleted.
    Does **not** disable the distribution.
    """
    cf = ctx.client("cloudfront")
    try:
        resp = cf.get_distribution_config(Id=distribution_id)
        config = resp["DistributionConfig"]
        etag = resp["ETag"]

        if not _strip_lambda_edge_associations(config):
            return  # Nothing to remove

        cf.update_distribution(Id=distribution_id, DistributionConfig=config, IfMatch=etag)
        _wait_distribution_deployed(cf, distribution_id)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchDistribution":
            return
        raise


def disable_and_delete_distribution(ctx: AWSContext, state: CdnState) -> None:
    """Disable, delete the CloudFront distribution, and remove OACs.

    Assumes Lambda@Edge associations have already been removed via
    :func:`remove_edge_associations`.
    """
    import contextlib

    cf = ctx.client("cloudfront")

    # Disable
    try:
        resp = cf.get_distribution_config(Id=state.distribution_id)
        config = resp["DistributionConfig"]
        etag = resp["ETag"]

        if config["Enabled"]:
            config["Enabled"] = False
            cf.update_distribution(
                Id=state.distribution_id,
                DistributionConfig=config,
                IfMatch=etag,
            )
            _wait_distribution_deployed(cf, state.distribution_id)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchDistribution":
            raise
        return  # Already gone

    # Delete distribution
    with contextlib.suppress(Exception):
        _finish_delete_distribution(ctx, state.distribution_id)

    # Delete OACs
    for oac_id in [state.oac_id, state.lambda_oac_id]:
        if oac_id:
            with contextlib.suppress(Exception):
                _delete_origin_access_control(ctx, oac_id)


def destroy(ctx: AWSContext, state: CdnState) -> None:
    """Delete CloudFront distribution and OACs (convenience wrapper)."""
    remove_edge_associations(ctx, state.distribution_id)
    disable_and_delete_distribution(ctx, state)


def wait_for_deployed(
    ctx: AWSContext,
    distribution_id: str,
    max_wait: int = 600,
    poll_interval: int = 15,
    on_poll: Callable[[float], None] | None = None,
) -> str:
    """Poll until CloudFront distribution status is 'Deployed'.

    Args:
        on_poll: Optional callback invoked each poll with elapsed seconds.

    Returns the final status string.
    """
    start = time.time()
    while time.time() - start < max_wait:
        result = _get_distribution(ctx, distribution_id)
        if result["status"] == "Deployed":
            return "Deployed"
        if on_poll:
            on_poll(time.time() - start)
        time.sleep(poll_interval)
    return result["status"]


def get_status(ctx: AWSContext, state: CdnState) -> list[ResourceStatus]:
    """Return CloudFront distribution status."""
    dist_id = state.distribution_id
    try:
        result = _get_distribution(ctx, dist_id)
        status = result["status"]
        if status == "Deployed":
            return [ResourceStatus("CloudFront", dist_id, "[green]Deployed[/green]")]
        else:
            return [ResourceStatus("CloudFront", dist_id, f"[yellow]{status}[/yellow]")]
    except Exception:
        return [ResourceStatus("CloudFront", dist_id, "[red]Not Found[/red]")]


def _create_origin_access_control(
    ctx: AWSContext,
    name: str,
    origin_type: str = "s3",
) -> str:
    """Create an Origin Access Control. Returns the OAC ID."""
    cf = ctx.client("cloudfront")
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
    ctx: AWSContext,
    bucket_name: str,
    region: str,
    oac_id: str,
    agentcore_region: str | None = None,
    edge_function_arn: str | None = None,
    index_document: str = "index.html",
    api_prefix: str = "/api",
    comment: str = "",
    tags: dict[str, str] | None = None,
) -> dict:
    """Create a CloudFront distribution."""
    cf = ctx.client("cloudfront")
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

    if agentcore_region:
        agentcore_domain = f"bedrock-agentcore.{agentcore_region}.amazonaws.com"
        agentcore_origin_id = "AgentCore-API"

        agentcore_origin = {
            "Id": agentcore_origin_id,
            "DomainName": agentcore_domain,
            "CustomOriginConfig": {
                "HTTPPort": 80,
                "HTTPSPort": 443,
                "OriginProtocolPolicy": "https-only",
                "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
            },
        }
        origins.append(agentcore_origin)

        api_pattern = f"{api_prefix}/*" if not api_prefix.endswith("/*") else api_prefix
        # CachingDisabled + AllViewer — Lambda@Edge rewrites Host and signs,
        # so we forward everything from the viewer.
        _CACHE_POLICY_CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
        _ORP_ALL_VIEWER = "216adef6-5c7f-47e4-b989-5492eafa07d3"

        api_cache_behavior = {
            "PathPattern": api_pattern,
            "TargetOriginId": agentcore_origin_id,
            "ViewerProtocolPolicy": "https-only",
            "AllowedMethods": {
                "Quantity": 7,
                "Items": ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"],
                "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
            },
            "Compress": True,
            "CachePolicyId": _CACHE_POLICY_CACHING_DISABLED,
            "OriginRequestPolicyId": _ORP_ALL_VIEWER,
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


def _get_distribution(ctx: AWSContext, distribution_id: str) -> dict:
    """Get distribution details."""
    cf = ctx.client("cloudfront")
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
    ctx: AWSContext,
    distribution_id: str,
    paths: list[str] | None = None,
) -> None:
    """Create a CloudFront cache invalidation.

    Args:
        distribution_id: CloudFront distribution ID.
        paths: List of paths to invalidate. Defaults to ``["/*"]``.
    """
    import contextlib

    cf = ctx.client("cloudfront")
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


def _finish_delete_distribution(ctx: AWSContext, distribution_id: str) -> None:
    """Delete a CloudFront distribution that is already disabled and Deployed."""
    cf = ctx.client("cloudfront")
    try:
        resp = cf.get_distribution(Id=distribution_id)
        etag = resp["ETag"]
        cf.delete_distribution(Id=distribution_id, IfMatch=etag)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchDistribution":
            return
        raise


def _strip_lambda_edge_associations(config: dict) -> bool:
    """Remove all Lambda@Edge function associations from a distribution config.

    Returns True if any associations were removed.
    """
    changed = False

    # Default cache behavior
    default = config.get("DefaultCacheBehavior", {})
    if default.get("LambdaFunctionAssociations", {}).get("Quantity", 0) > 0:
        default["LambdaFunctionAssociations"] = {"Quantity": 0, "Items": []}
        changed = True

    # Additional cache behaviors
    for behavior in config.get("CacheBehaviors", {}).get("Items", []):
        if behavior.get("LambdaFunctionAssociations", {}).get("Quantity", 0) > 0:
            behavior["LambdaFunctionAssociations"] = {"Quantity": 0, "Items": []}
            changed = True

    return changed


def _wait_distribution_deployed(
    cf,
    distribution_id: str,
    max_wait: int = 600,
    poll_interval: int = 15,
) -> None:
    """Poll until a CloudFront distribution reaches 'Deployed' status."""
    start = time.time()
    while time.time() - start < max_wait:
        resp = cf.get_distribution(Id=distribution_id)
        if resp["Distribution"]["Status"] == "Deployed":
            return
        time.sleep(poll_interval)


def _delete_origin_access_control(ctx: AWSContext, oac_id: str) -> None:
    """Delete an Origin Access Control."""
    cf = ctx.client("cloudfront")
    try:
        resp = cf.get_origin_access_control(Id=oac_id)
        etag = resp["ETag"]
        cf.delete_origin_access_control(Id=oac_id, IfMatch=etag)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchOriginAccessControl":
            return
        raise
