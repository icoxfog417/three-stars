"""Tests for storage resource module."""

from __future__ import annotations

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from three_stars.resources.storage import (
    _create_bucket,
    _empty_bucket,
    _upload_directory,
    destroy,
)
from three_stars.state import StorageState


@mock_aws
class TestStorage:
    def test_create_bucket_us_east_1(self):
        session = boto3.Session(region_name="us-east-1")
        bucket = _create_bucket(session, "test-bucket", "us-east-1")
        assert bucket == "test-bucket"

        s3 = session.client("s3")
        response = s3.head_bucket(Bucket="test-bucket")
        assert response["ResponseMetadata"]["HTTPStatusCode"] == 200

    def test_create_bucket_other_region(self):
        session = boto3.Session(region_name="eu-west-1")
        bucket = _create_bucket(session, "test-bucket-eu", "eu-west-1")
        assert bucket == "test-bucket-eu"

    def test_create_bucket_idempotent(self):
        session = boto3.Session(region_name="us-east-1")
        _create_bucket(session, "test-bucket", "us-east-1")
        _create_bucket(session, "test-bucket", "us-east-1")

    def test_upload_directory(self, tmp_path):
        session = boto3.Session(region_name="us-east-1")
        _create_bucket(session, "test-bucket", "us-east-1")

        (tmp_path / "index.html").write_text("<html></html>")
        (tmp_path / "style.css").write_text("body {}")
        sub = tmp_path / "assets"
        sub.mkdir()
        (sub / "app.js").write_text("console.log('hi')")

        count = _upload_directory(session, "test-bucket", tmp_path)
        assert count == 3

        s3 = session.client("s3")
        objects = s3.list_objects_v2(Bucket="test-bucket")
        keys = [obj["Key"] for obj in objects["Contents"]]
        assert "index.html" in keys
        assert "style.css" in keys
        assert "assets/app.js" in keys

    def test_upload_sets_content_type(self, tmp_path):
        session = boto3.Session(region_name="us-east-1")
        _create_bucket(session, "test-bucket", "us-east-1")

        (tmp_path / "index.html").write_text("<html></html>")
        _upload_directory(session, "test-bucket", tmp_path)

        s3 = session.client("s3")
        resp = s3.head_object(Bucket="test-bucket", Key="index.html")
        assert resp["ContentType"] == "text/html"

    def test_empty_bucket(self, tmp_path):
        session = boto3.Session(region_name="us-east-1")
        _create_bucket(session, "test-bucket", "us-east-1")

        (tmp_path / "file1.txt").write_text("hello")
        (tmp_path / "file2.txt").write_text("world")
        _upload_directory(session, "test-bucket", tmp_path)

        count = _empty_bucket(session, "test-bucket")
        assert count == 2

        s3 = session.client("s3")
        objects = s3.list_objects_v2(Bucket="test-bucket")
        assert objects.get("KeyCount", 0) == 0

    def test_destroy_bucket(self, tmp_path):
        session = boto3.Session(region_name="us-east-1")
        _create_bucket(session, "test-bucket", "us-east-1")

        (tmp_path / "file.txt").write_text("data")
        _upload_directory(session, "test-bucket", tmp_path)

        destroy(session, StorageState(s3_bucket="test-bucket"))

        s3 = session.client("s3")
        with pytest.raises(ClientError):
            s3.head_bucket(Bucket="test-bucket")

    def test_destroy_nonexistent_bucket(self):
        session = boto3.Session(region_name="us-east-1")
        destroy(session, StorageState(s3_bucket="nonexistent-bucket"))
