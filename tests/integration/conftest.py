"""Shared fixtures for integration tests (real AWS)."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _aws_credentials():
    """Override dummy credentials — use real AWS credentials."""
    saved = {}
    for key in [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SECURITY_TOKEN",
        "AWS_SESSION_TOKEN",
    ]:
        saved[key] = os.environ.pop(key, None)
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    yield
    for key, val in saved.items():
        if val is not None:
            os.environ[key] = val
