"""Tests for storage resource module."""

from __future__ import annotations

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from three_stars.config import AppConfig, ProjectConfig
from three_stars.naming import ResourceNames
from three_stars.resources import storage
from three_stars.resources._base import AWSContext
from three_stars.resources.storage import (
    _create_bucket,
    _empty_bucket,
    _upload_directory,
)
from three_stars.state import StorageState


def _make_ctx(region="us-east-1"):
    return AWSContext(boto3.Session(region_name=region))


def _make_names():
    return ResourceNames(
        prefix="sss-test",
        bucket="sss-test-abc12345",
        agentcore_role="sss-test-role",
        agent_name="sss_test_agent",
        endpoint_name="sss_test_endpoint",
        lambda_role="sss-test-lambda-role",
        lambda_function="sss-test-api-bridge",
        edge_role="sss-test-edge-role",
        edge_function="sss-test-edge-sha256",
        memory="sss_test_memory",
    )


def _make_config(tmp_path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "agent.py").write_text("pass")
    (agent_dir / "requirements.txt").write_text("")
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "index.html").write_text("<html></html>")
    (app_dir / "style.css").write_text("body {}")
    return ProjectConfig(
        name="test-app",
        region="us-east-1",
        app=AppConfig(source="app"),
        project_dir=tmp_path,
    )


@mock_aws
class TestStorageHelpers:
    """Tests for internal helper functions."""

    def test_create_bucket_us_east_1(self):
        ctx = _make_ctx()
        bucket = _create_bucket(ctx, "test-bucket", "us-east-1")
        assert bucket == "test-bucket"

        s3 = ctx.client("s3")
        response = s3.head_bucket(Bucket="test-bucket")
        assert response["ResponseMetadata"]["HTTPStatusCode"] == 200

    def test_create_bucket_other_region(self):
        ctx = _make_ctx("eu-west-1")
        bucket = _create_bucket(ctx, "test-bucket-eu", "eu-west-1")
        assert bucket == "test-bucket-eu"

    def test_create_bucket_idempotent(self):
        ctx = _make_ctx()
        _create_bucket(ctx, "test-bucket", "us-east-1")
        _create_bucket(ctx, "test-bucket", "us-east-1")

    def test_upload_directory(self, tmp_path):
        ctx = _make_ctx()
        _create_bucket(ctx, "test-bucket", "us-east-1")

        (tmp_path / "index.html").write_text("<html></html>")
        (tmp_path / "style.css").write_text("body {}")
        sub = tmp_path / "assets"
        sub.mkdir()
        (sub / "app.js").write_text("console.log('hi')")

        count = _upload_directory(ctx, "test-bucket", tmp_path)
        assert count == 3

        s3 = ctx.client("s3")
        objects = s3.list_objects_v2(Bucket="test-bucket")
        keys = [obj["Key"] for obj in objects["Contents"]]
        assert "index.html" in keys
        assert "style.css" in keys
        assert "assets/app.js" in keys

    def test_upload_sets_content_type(self, tmp_path):
        ctx = _make_ctx()
        _create_bucket(ctx, "test-bucket", "us-east-1")

        (tmp_path / "index.html").write_text("<html></html>")
        _upload_directory(ctx, "test-bucket", tmp_path)

        s3 = ctx.client("s3")
        resp = s3.head_object(Bucket="test-bucket", Key="index.html")
        assert resp["ContentType"] == "text/html"

    def test_empty_bucket(self, tmp_path):
        ctx = _make_ctx()
        _create_bucket(ctx, "test-bucket", "us-east-1")

        (tmp_path / "file1.txt").write_text("hello")
        (tmp_path / "file2.txt").write_text("world")
        _upload_directory(ctx, "test-bucket", tmp_path)

        count = _empty_bucket(ctx, "test-bucket")
        assert count == 2

        s3 = ctx.client("s3")
        objects = s3.list_objects_v2(Bucket="test-bucket")
        assert objects.get("KeyCount", 0) == 0


@mock_aws
class TestStorageContract:
    """Contract tests for deploy/destroy/get_status."""

    def test_deploy_returns_state(self, tmp_path):
        ctx = _make_ctx()
        config = _make_config(tmp_path)
        names = _make_names()

        state = storage.deploy(ctx, config, names)

        assert isinstance(state, StorageState)
        assert state.s3_bucket == "sss-test-abc12345"
        # Verify bucket exists
        s3 = ctx.client("s3")
        resp = s3.head_bucket(Bucket=state.s3_bucket)
        assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200

    def test_deploy_update_idempotent(self, tmp_path):
        ctx = _make_ctx()
        config = _make_config(tmp_path)
        names = _make_names()

        state1 = storage.deploy(ctx, config, names)
        state2 = storage.deploy(ctx, config, names)

        assert state1.s3_bucket == state2.s3_bucket

    def test_destroy_cleans_up(self, tmp_path):
        ctx = _make_ctx()
        config = _make_config(tmp_path)
        names = _make_names()

        state = storage.deploy(ctx, config, names)
        storage.destroy(ctx, state)

        s3 = ctx.client("s3")
        with pytest.raises(ClientError):
            s3.head_bucket(Bucket=state.s3_bucket)

    def test_destroy_idempotent(self, tmp_path):
        ctx = _make_ctx()
        config = _make_config(tmp_path)
        names = _make_names()

        state = storage.deploy(ctx, config, names)
        storage.destroy(ctx, state)
        # Second destroy should not raise
        storage.destroy(ctx, state)

    def test_get_status(self, tmp_path):
        ctx = _make_ctx()
        config = _make_config(tmp_path)
        names = _make_names()

        state = storage.deploy(ctx, config, names)
        rows = storage.get_status(ctx, state)

        assert len(rows) == 1
        assert "Active" in rows[0].status
        assert rows[0].resource == "S3 Bucket"

    def test_get_status_not_found(self):
        ctx = _make_ctx()
        state = StorageState(s3_bucket="nonexistent-bucket")
        rows = storage.get_status(ctx, state)

        assert len(rows) == 1
        assert "Not Found" in rows[0].status
