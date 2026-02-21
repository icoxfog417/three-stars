"""Tests for S3 operations."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from three_stars.aws.s3 import (
    create_bucket,
    delete_bucket,
    empty_bucket,
    upload_directory,
)


@mock_aws
class TestS3:
    def test_create_bucket_us_east_1(self):
        session = boto3.Session(region_name="us-east-1")
        bucket = create_bucket(session, "test-bucket", "us-east-1")
        assert bucket == "test-bucket"

        # Verify bucket exists
        s3 = session.client("s3")
        response = s3.head_bucket(Bucket="test-bucket")
        assert response["ResponseMetadata"]["HTTPStatusCode"] == 200

    def test_create_bucket_other_region(self):
        session = boto3.Session(region_name="eu-west-1")
        bucket = create_bucket(session, "test-bucket-eu", "eu-west-1")
        assert bucket == "test-bucket-eu"

    def test_create_bucket_idempotent(self):
        session = boto3.Session(region_name="us-east-1")
        create_bucket(session, "test-bucket", "us-east-1")
        # Should not raise
        create_bucket(session, "test-bucket", "us-east-1")

    def test_upload_directory(self, tmp_path):
        session = boto3.Session(region_name="us-east-1")
        create_bucket(session, "test-bucket", "us-east-1")

        # Create test files
        (tmp_path / "index.html").write_text("<html></html>")
        (tmp_path / "style.css").write_text("body {}")
        sub = tmp_path / "assets"
        sub.mkdir()
        (sub / "app.js").write_text("console.log('hi')")

        count = upload_directory(session, "test-bucket", tmp_path)
        assert count == 3

        # Verify files exist
        s3 = session.client("s3")
        objects = s3.list_objects_v2(Bucket="test-bucket")
        keys = [obj["Key"] for obj in objects["Contents"]]
        assert "index.html" in keys
        assert "style.css" in keys
        assert "assets/app.js" in keys

    def test_upload_sets_content_type(self, tmp_path):
        session = boto3.Session(region_name="us-east-1")
        create_bucket(session, "test-bucket", "us-east-1")

        (tmp_path / "index.html").write_text("<html></html>")
        upload_directory(session, "test-bucket", tmp_path)

        s3 = session.client("s3")
        resp = s3.head_object(Bucket="test-bucket", Key="index.html")
        assert resp["ContentType"] == "text/html"

    def test_empty_bucket(self, tmp_path):
        session = boto3.Session(region_name="us-east-1")
        create_bucket(session, "test-bucket", "us-east-1")

        (tmp_path / "file1.txt").write_text("hello")
        (tmp_path / "file2.txt").write_text("world")
        upload_directory(session, "test-bucket", tmp_path)

        count = empty_bucket(session, "test-bucket")
        assert count == 2

        s3 = session.client("s3")
        objects = s3.list_objects_v2(Bucket="test-bucket")
        assert objects.get("KeyCount", 0) == 0

    def test_delete_bucket(self, tmp_path):
        session = boto3.Session(region_name="us-east-1")
        create_bucket(session, "test-bucket", "us-east-1")

        (tmp_path / "file.txt").write_text("data")
        upload_directory(session, "test-bucket", tmp_path)

        delete_bucket(session, "test-bucket")

        from botocore.exceptions import ClientError

        s3 = session.client("s3")
        with pytest.raises(ClientError):
            s3.head_bucket(Bucket="test-bucket")

    def test_delete_nonexistent_bucket(self):
        session = boto3.Session(region_name="us-east-1")
        # Should not raise
        delete_bucket(session, "nonexistent-bucket")
