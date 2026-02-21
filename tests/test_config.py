"""Tests for configuration loading and validation."""

from __future__ import annotations

import pytest
import yaml

from three_stars.config import ConfigError, load_config


class TestLoadConfig:
    def test_load_valid_config(self, project_dir):
        config = load_config(project_dir)
        assert config.name == "test-app"
        assert config.region == "us-east-1"
        assert config.agent.model == "anthropic.claude-sonnet-4-20250514"
        assert config.agent.memory == 512
        assert config.app.index == "index.html"
        assert config.api.prefix == "/api"

    def test_missing_config_file(self, tmp_path):
        with pytest.raises(ConfigError, match="Config file not found"):
            load_config(tmp_path)

    def test_invalid_yaml(self, tmp_path):
        (tmp_path / "three-stars.yml").write_text("{{invalid")
        with pytest.raises(ConfigError, match="Invalid YAML"):
            load_config(tmp_path)

    def test_missing_name(self, project_dir):
        config_path = project_dir / "three-stars.yml"
        with open(config_path) as f:
            data = yaml.safe_load(f)
        del data["name"]
        with open(config_path, "w") as f:
            yaml.dump(data, f)

        with pytest.raises(ConfigError, match="'name' is required"):
            load_config(project_dir)

    def test_invalid_name_characters(self, project_dir):
        config_path = project_dir / "three-stars.yml"
        with open(config_path) as f:
            data = yaml.safe_load(f)
        data["name"] = "invalid name with spaces!"
        with open(config_path, "w") as f:
            yaml.dump(data, f)

        with pytest.raises(ConfigError, match="invalid characters"):
            load_config(project_dir)

    def test_name_too_long(self, project_dir):
        config_path = project_dir / "three-stars.yml"
        with open(config_path) as f:
            data = yaml.safe_load(f)
        data["name"] = "a" * 51
        with open(config_path, "w") as f:
            yaml.dump(data, f)

        with pytest.raises(ConfigError, match="50 characters"):
            load_config(project_dir)

    def test_region_override(self, project_dir):
        config = load_config(project_dir, region_override="eu-west-1")
        assert config.region == "eu-west-1"

    def test_defaults(self, tmp_path):
        (tmp_path / "three-stars.yml").write_text("name: minimal-app\n")
        (tmp_path / "agent").mkdir()
        (tmp_path / "app").mkdir()

        config = load_config(tmp_path)
        assert config.name == "minimal-app"
        assert config.region == "us-east-1"
        assert config.agent.source == "./agent"
        assert config.agent.memory == 512
        assert config.app.source == "./app"
        assert config.app.index == "index.html"
        assert config.api.prefix == "/api"

    def test_missing_agent_dir(self, project_dir):
        import shutil

        shutil.rmtree(project_dir / "agent")
        with pytest.raises(ConfigError, match="Agent source directory not found"):
            load_config(project_dir)

    def test_missing_app_dir(self, project_dir):
        import shutil

        shutil.rmtree(project_dir / "app")
        with pytest.raises(ConfigError, match="App source directory not found"):
            load_config(project_dir)

    def test_invalid_memory_low(self, project_dir):
        config_path = project_dir / "three-stars.yml"
        with open(config_path) as f:
            data = yaml.safe_load(f)
        data["agent"]["memory"] = 64
        with open(config_path, "w") as f:
            yaml.dump(data, f)

        with pytest.raises(ConfigError, match="at least 128"):
            load_config(project_dir)

    def test_invalid_api_prefix(self, project_dir):
        config_path = project_dir / "three-stars.yml"
        with open(config_path) as f:
            data = yaml.safe_load(f)
        data["api"] = {"prefix": "api"}
        with open(config_path, "w") as f:
            yaml.dump(data, f)

        with pytest.raises(ConfigError, match="must start with '/'"):
            load_config(project_dir)

    def test_not_a_mapping(self, tmp_path):
        (tmp_path / "three-stars.yml").write_text("- item1\n- item2\n")
        with pytest.raises(ConfigError, match="YAML mapping"):
            load_config(tmp_path)
