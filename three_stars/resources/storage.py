"""Storage resource module — S3 bucket + frontend upload."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path

from botocore.exceptions import ClientError

from three_stars.config import ProjectConfig, resolve_path
from three_stars.naming import ResourceNames
from three_stars.resources import ResourceStatus
from three_stars.resources._base import AWSContext
from three_stars.state import StorageState


def deploy(
    ctx: AWSContext,
    config: ProjectConfig,
    names: ResourceNames,
    *,
    tags: list[dict[str, str]] | None = None,
) -> StorageState:
    """Create S3 bucket and upload frontend files.

    Returns:
        StorageState capturing the bucket name.
    """
    bucket_name = names.bucket

    _create_bucket(ctx, bucket_name, config.region)

    if tags:
        _tag_bucket(ctx, bucket_name, tags)

    # Upload frontend files
    app_path = resolve_path(config, config.app.source)
    _upload_directory(ctx, bucket_name, app_path)

    return StorageState(s3_bucket=bucket_name)


def destroy(ctx: AWSContext, state: StorageState) -> None:
    """Empty and delete S3 bucket."""
    s3 = ctx.client("s3")
    try:
        _empty_bucket(ctx, state.s3_bucket)
        s3.delete_bucket(Bucket=state.s3_bucket)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchBucket":
            return
        raise


def get_status(ctx: AWSContext, state: StorageState) -> list[ResourceStatus]:
    """Return status rows for storage resources."""
    try:
        s3 = ctx.client("s3")
        s3.head_bucket(Bucket=state.s3_bucket)
        return [ResourceStatus("S3 Bucket", state.s3_bucket, "[green]Active[/green]")]
    except Exception:
        return [ResourceStatus("S3 Bucket", state.s3_bucket, "[red]Not Found[/red]")]


def set_bucket_policy_for_cloudfront(
    ctx: AWSContext,
    bucket_name: str,
    cloudfront_distribution_arn: str,
) -> None:
    """Set bucket policy to allow CloudFront OAC access."""
    s3 = ctx.client("s3")
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowCloudFrontOAC",
                "Effect": "Allow",
                "Principal": {"Service": "cloudfront.amazonaws.com"},
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket_name}/*",
                "Condition": {
                    "StringEquals": {
                        "AWS:SourceArn": cloudfront_distribution_arn,
                    }
                },
            }
        ],
    }
    s3.put_bucket_policy(Bucket=bucket_name, Policy=json.dumps(policy))


def _create_bucket(ctx: AWSContext, bucket_name: str, region: str) -> str:
    """Create an S3 bucket. Idempotent."""
    s3 = ctx.client("s3")
    try:
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket_name)
        else:
            s3.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            pass
        else:
            raise

    s3.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )

    return bucket_name


def _tag_bucket(
    ctx: AWSContext,
    bucket_name: str,
    tags: list[dict[str, str]],
) -> None:
    """Apply tags to an S3 bucket."""
    s3 = ctx.client("s3")
    s3.put_bucket_tagging(
        Bucket=bucket_name,
        Tagging={"TagSet": tags},
    )


def _upload_directory(
    ctx: AWSContext,
    bucket_name: str,
    local_dir: str | Path,
    prefix: str = "",
) -> int:
    """Upload all files from a local directory to S3."""
    s3 = ctx.client("s3")
    local_path = Path(local_dir)
    count = 0

    for file_path in sorted(local_path.rglob("*")):
        if file_path.is_dir():
            continue

        relative = file_path.relative_to(local_path)
        key = f"{prefix}{relative}" if prefix else str(relative)

        content_type, _ = mimetypes.guess_type(str(file_path))
        if content_type is None:
            content_type = "application/octet-stream"

        s3.upload_file(
            str(file_path),
            bucket_name,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        count += 1

    return count


def _empty_bucket(ctx: AWSContext, bucket_name: str) -> int:
    """Delete all objects in an S3 bucket."""
    s3 = ctx.client("s3")
    count = 0

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket_name):
        objects = page.get("Contents", [])
        if not objects:
            continue
        delete_keys = [{"Key": obj["Key"]} for obj in objects]
        s3.delete_objects(Bucket=bucket_name, Delete={"Objects": delete_keys})
        count += len(delete_keys)

    return count
