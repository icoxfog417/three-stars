"""Tests for AgentCore resource module."""

from __future__ import annotations

import zipfile
from io import BytesIO

import boto3
import pytest
from moto import mock_aws

from three_stars.resources.agentcore import (
    _create_iam_role,
    _delete_iam_role,
    _package_agent,
)


class TestPackageAgent:
    def test_creates_valid_zip(self, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text("def handler(): pass")
        (agent_dir / "requirements.txt").write_text("boto3")

        result = _package_agent(agent_dir)

        zf = zipfile.ZipFile(BytesIO(result))
        names = zf.namelist()
        assert "agent.py" in names
        assert "requirements.txt" in names

    def test_excludes_pycache(self, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text("def handler(): pass")
        pycache = agent_dir / "__pycache__"
        pycache.mkdir()
        (pycache / "agent.cpython-311.pyc").write_bytes(b"\x00")

        result = _package_agent(agent_dir)
        zf = zipfile.ZipFile(BytesIO(result))
        assert not any("__pycache__" in name for name in zf.namelist())

    def test_excludes_hidden_files(self, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text("def handler(): pass")
        (agent_dir / ".env").write_text("SECRET=123")

        result = _package_agent(agent_dir)
        zf = zipfile.ZipFile(BytesIO(result))
        assert ".env" not in zf.namelist()

    def test_preserves_subdirectories(self, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text("import lib")
        lib_dir = agent_dir / "lib"
        lib_dir.mkdir()
        (lib_dir / "helper.py").write_text("def help(): pass")

        result = _package_agent(agent_dir)
        zf = zipfile.ZipFile(BytesIO(result))
        assert "lib/helper.py" in zf.namelist()


@mock_aws
class TestIAMRole:
    def test_create_role(self):
        session = boto3.Session(region_name="us-east-1")
        arn = _create_iam_role(session, "test-role", "123456789012")
        assert "test-role" in arn

    def test_create_role_idempotent(self):
        session = boto3.Session(region_name="us-east-1")
        arn1 = _create_iam_role(session, "test-role", "123456789012")
        arn2 = _create_iam_role(session, "test-role", "123456789012")
        assert arn1 == arn2

    def test_delete_role(self):
        session = boto3.Session(region_name="us-east-1")
        _create_iam_role(session, "test-role", "123456789012")
        _delete_iam_role(session, "test-role")

        iam = session.client("iam")
        with pytest.raises(iam.exceptions.NoSuchEntityException):
            iam.get_role(RoleName="test-role")

    def test_delete_nonexistent_role(self):
        session = boto3.Session(region_name="us-east-1")
        _delete_iam_role(session, "nonexistent-role")
