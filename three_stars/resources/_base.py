"""Shared helpers for resource modules."""

from __future__ import annotations

import boto3


def create_session(
    region: str | None = None,
    profile: str | None = None,
) -> boto3.Session:
    """Create a boto3 session."""
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
