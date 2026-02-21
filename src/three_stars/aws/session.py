"""AWS session management."""

from __future__ import annotations

import boto3


def create_session(
    region: str | None = None,
    profile: str | None = None,
) -> boto3.Session:
    """Create a boto3 session.

    Args:
        region: AWS region name.
        profile: AWS CLI profile name.

    Returns:
        Configured boto3.Session.
    """
    kwargs: dict = {}
    if region:
        kwargs["region_name"] = region
    if profile:
        kwargs["profile_name"] = profile
    return boto3.Session(**kwargs)


def get_account_id(session: boto3.Session) -> str:
    """Get the AWS account ID for the current session."""
    sts = session.client("sts")
    return sts.get_caller_identity()["Account"]


def get_region(session: boto3.Session) -> str:
    """Get the region from the session."""
    return session.region_name or "us-east-1"
