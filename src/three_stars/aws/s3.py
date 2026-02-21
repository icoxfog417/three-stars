"""S3 operations for static frontend hosting."""

from __future__ import annotations

import mimetypes
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def create_bucket(session: boto3.Session, bucket_name: str, region: str) -> str:
    """Create an S3 bucket for static file hosting.

    Returns the bucket name. Idempotent — skips creation if bucket exists.
    """
    s3 = session.client("s3")
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
            pass  # Bucket already exists, continue
        else:
            raise

    # Block public access
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


def tag_bucket(
    session: boto3.Session,
    bucket_name: str,
    tags: list[dict[str, str]],
) -> None:
    """Apply tags to an S3 bucket.

    Args:
        session: boto3 session.
        bucket_name: S3 bucket name.
        tags: List of {"Key": k, "Value": v} dicts.
    """
    s3 = session.client("s3")
    s3.put_bucket_tagging(
        Bucket=bucket_name,
        Tagging={"TagSet": tags},
    )


def set_bucket_policy_for_cloudfront(
    session: boto3.Session,
    bucket_name: str,
    cloudfront_distribution_arn: str,
) -> None:
    """Set bucket policy to allow CloudFront OAC access."""
    import json

    s3 = session.client("s3")
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


def upload_directory(
    session: boto3.Session,
    bucket_name: str,
    local_dir: str | Path,
    prefix: str = "",
    on_progress: callable | None = None,
) -> int:
    """Upload all files from a local directory to S3.

    Args:
        session: boto3 session.
        bucket_name: Target S3 bucket.
        local_dir: Local directory to upload.
        prefix: S3 key prefix.
        on_progress: Called with (filename,) after each upload.

    Returns:
        Number of files uploaded.
    """
    s3 = session.client("s3")
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
        if on_progress:
            on_progress(str(relative))

    return count


def empty_bucket(session: boto3.Session, bucket_name: str) -> int:
    """Delete all objects in an S3 bucket.

    Returns the number of objects deleted.
    """
    s3 = session.client("s3")
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


def delete_bucket(session: boto3.Session, bucket_name: str) -> None:
    """Empty and delete an S3 bucket."""
    s3 = session.client("s3")
    try:
        empty_bucket(session, bucket_name)
        s3.delete_bucket(Bucket=bucket_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchBucket":
            return
        raise
