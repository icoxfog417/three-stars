"""Tests for CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from three_stars.cli import main


class TestCLI:
    def test_version(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "three-stars" in result.output
        assert "0.1.0" in result.output

    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "deploy" in result.output
        assert "destroy" in result.output
        assert "status" in result.output
        assert "init" in result.output

    def test_deploy_no_config(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, ["deploy", str(tmp_path), "--yes"])
        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_init_creates_project(self, tmp_path):
        runner = CliRunner()
        with (
            runner.isolated_filesystem(temp_dir=tmp_path),
            patch("three_stars.init.TEMPLATES_DIR", tmp_path / "_templates"),
        ):
            # Create a minimal template
            template_dir = tmp_path / "_templates" / "starter"
            template_dir.mkdir(parents=True)
            (template_dir / "three-stars.yml").write_text("name: my-ai-app\n")
            agent_dir = template_dir / "agent"
            agent_dir.mkdir()
            (agent_dir / "agent.py").write_text("# agent\n")
            app_dir = template_dir / "app"
            app_dir.mkdir()
            (app_dir / "index.html").write_text("<html></html>\n")

            result = runner.invoke(main, ["init", "test-project"])
            assert result.exit_code == 0
            assert "Created project" in result.output

    def test_init_existing_dir(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            # pre-create the target directory in the CWD
            (Path(td) / "my-app").mkdir()
            with patch("three_stars.init.TEMPLATES_DIR", tmp_path / "_templates"):
                template_dir = tmp_path / "_templates" / "starter"
                template_dir.mkdir(parents=True)
                (template_dir / "three-stars.yml").write_text("name: my-ai-app\n")
                result = runner.invoke(main, ["init", "my-app"])
                assert result.exit_code == 1

    def test_destroy_no_state(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, ["destroy", str(tmp_path), "--yes"])
        assert result.exit_code == 0
        assert "No deployment found" in result.output

    def test_status_no_state(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, ["status", str(tmp_path)])
        assert result.exit_code == 0
        assert "No deployment found" in result.output
