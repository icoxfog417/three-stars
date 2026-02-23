"""Shared test fixtures for three-stars."""

from __future__ import annotations

import os

import pytest
import yaml

from three_stars.config import ProjectConfig
from three_stars.naming import ResourceNames, compute_names

TEST_ACCOUNT_ID = "123456789012"


def make_test_names(project_name: str = "test") -> ResourceNames:
    """Build ResourceNames via compute_names for test use.

    Call with a project name to get correctly-derived resource names
    instead of hardcoding them in each test file.
    """
    config = ProjectConfig(name=project_name, region="us-east-1")
    return compute_names(config, TEST_ACCOUNT_ID)


@pytest.fixture(autouse=True)
def _aws_credentials():
    """Set dummy AWS credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    yield
    for key in [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SECURITY_TOKEN",
        "AWS_SESSION_TOKEN",
    ]:
        os.environ.pop(key, None)


@pytest.fixture
def sample_config_dict():
    """Return a minimal valid config dictionary."""
    return {
        "name": "test-app",
        "region": "us-east-1",
        "agent": {
            "source": "./agent",
            "description": "Test agent",
        },
        "app": {
            "source": "./app",
            "index": "index.html",
        },
        "api": {
            "prefix": "/api",
        },
    }


@pytest.fixture
def project_dir(sample_config_dict, tmp_path):
    """Create a temporary project directory with config, agent, and app."""
    config_path = tmp_path / "three-stars.yml"
    with open(config_path, "w") as f:
        yaml.dump(sample_config_dict, f)

    # Create agent directory with a simple agent
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "agent.py").write_text(
        '"""Starter agent."""\n\ndef handler(event):\n    return {"message": "Hello"}\n'
    )
    (agent_dir / "requirements.txt").write_text("")

    # Create app directory with index.html
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "index.html").write_text("<!DOCTYPE html><html><body>Hello</body></html>")
    (app_dir / "style.css").write_text("body { margin: 0; }")

    return tmp_path
