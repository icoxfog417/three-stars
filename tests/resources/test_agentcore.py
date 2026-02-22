"""Tests for AgentCore resource module."""

from __future__ import annotations

import subprocess
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from three_stars.resources.agentcore import (
    _create_iam_role,
    _delete_iam_role,
    _install_dependencies,
    _package_agent,
)


def _fake_uv_install(cmd, *, check=False, capture_output=False, text=False):
    """Simulate uv writing a package file into the --target directory."""
    target_idx = cmd.index("--target") + 1
    target_dir = Path(cmd[target_idx])
    pkg_dir = target_dir / "bedrock_agentcore"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text("# installed dep")
    return subprocess.CompletedProcess(cmd, 0)


class TestPackageAgent:
    @patch("three_stars.resources.agentcore.subprocess.run", side_effect=_fake_uv_install)
    def test_creates_valid_zip_with_deps(self, mock_run, tmp_path):
        """Zip must contain both source files and installed dependencies."""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text("def handler(): pass")
        (agent_dir / "requirements.txt").write_text("bedrock-agentcore")

        result = _package_agent(agent_dir)

        zf = zipfile.ZipFile(BytesIO(result))
        names = zf.namelist()
        assert "agent.py" in names
        assert "requirements.txt" in names
        assert "bedrock_agentcore/__init__.py" in names

    @patch("three_stars.resources.agentcore.subprocess.run", side_effect=_fake_uv_install)
    def test_excludes_pycache(self, mock_run, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text("def handler(): pass")
        (agent_dir / "requirements.txt").write_text("bedrock-agentcore")
        pycache = agent_dir / "__pycache__"
        pycache.mkdir()
        (pycache / "agent.cpython-311.pyc").write_bytes(b"\x00")

        result = _package_agent(agent_dir)
        zf = zipfile.ZipFile(BytesIO(result))
        assert not any("__pycache__" in name for name in zf.namelist())

    @patch("three_stars.resources.agentcore.subprocess.run", side_effect=_fake_uv_install)
    def test_excludes_hidden_files(self, mock_run, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text("def handler(): pass")
        (agent_dir / "requirements.txt").write_text("bedrock-agentcore")
        (agent_dir / ".env").write_text("SECRET=123")

        result = _package_agent(agent_dir)
        zf = zipfile.ZipFile(BytesIO(result))
        assert ".env" not in zf.namelist()

    @patch("three_stars.resources.agentcore.subprocess.run", side_effect=_fake_uv_install)
    def test_preserves_subdirectories(self, mock_run, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text("import lib")
        (agent_dir / "requirements.txt").write_text("bedrock-agentcore")
        lib_dir = agent_dir / "lib"
        lib_dir.mkdir()
        (lib_dir / "helper.py").write_text("def help(): pass")

        result = _package_agent(agent_dir)
        zf = zipfile.ZipFile(BytesIO(result))
        assert "lib/helper.py" in zf.namelist()

    def test_no_requirements_file(self, tmp_path):
        """Without requirements.txt, only source files are zipped (no subprocess)."""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text("def handler(): pass")

        with patch("three_stars.resources.agentcore.subprocess.run") as mock_run:
            result = _package_agent(agent_dir)
            mock_run.assert_not_called()

        zf = zipfile.ZipFile(BytesIO(result))
        assert "agent.py" in zf.namelist()

    @patch("three_stars.resources.agentcore.subprocess.run", side_effect=_fake_uv_install)
    def test_includes_dist_info(self, mock_run, tmp_path):
        """Dist-info directories must be kept for importlib.metadata resolution."""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text("def handler(): pass")
        (agent_dir / "requirements.txt").write_text("bedrock-agentcore")

        original = _fake_uv_install

        def install_with_distinfo(cmd, **kwargs):
            result = original(cmd, **kwargs)
            target_idx = cmd.index("--target") + 1
            target_dir = Path(cmd[target_idx])
            distinfo = target_dir / "bedrock_agentcore-1.0.0.dist-info"
            distinfo.mkdir(parents=True, exist_ok=True)
            (distinfo / "METADATA").write_text("Name: bedrock-agentcore")
            return result

        mock_run.side_effect = install_with_distinfo

        result = _package_agent(agent_dir)
        zf = zipfile.ZipFile(BytesIO(result))
        assert any(".dist-info" in name for name in zf.namelist())


class TestInstallDependencies:
    @patch("three_stars.resources.agentcore.subprocess.run", side_effect=_fake_uv_install)
    def test_uses_uv_by_default(self, mock_run, tmp_path):
        reqs = tmp_path / "requirements.txt"
        reqs.write_text("bedrock-agentcore")
        deps = tmp_path / "deps"
        deps.mkdir()

        _install_dependencies(reqs, deps)

        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "uv"
        assert "--python-platform" in call_args
        assert "aarch64-manylinux2014" in call_args

    def test_falls_back_to_pip(self, tmp_path):
        """When uv is not found, falls back to pip."""
        reqs = tmp_path / "requirements.txt"
        reqs.write_text("bedrock-agentcore")
        deps = tmp_path / "deps"
        deps.mkdir()

        call_count = 0

        def side_effect(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if cmd[0] == "uv":
                raise FileNotFoundError("uv not found")
            # pip call — simulate installing
            target_idx = cmd.index("--target") + 1
            target_dir = Path(cmd[target_idx])
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "dep.py").write_text("# dep")
            return subprocess.CompletedProcess(cmd, 0)

        with patch("three_stars.resources.agentcore.subprocess.run", side_effect=side_effect):
            _install_dependencies(reqs, deps)

        assert call_count == 2  # uv failed, pip succeeded

    def test_raises_when_neither_found(self, tmp_path):
        reqs = tmp_path / "requirements.txt"
        reqs.write_text("bedrock-agentcore")
        deps = tmp_path / "deps"
        deps.mkdir()

        with (
            patch(
                "three_stars.resources.agentcore.subprocess.run",
                side_effect=FileNotFoundError("not found"),
            ),
            pytest.raises(RuntimeError, match="Neither 'uv' nor 'pip' found"),
        ):
            _install_dependencies(reqs, deps)


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
