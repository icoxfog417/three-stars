"""Configuration loading and validation for three-stars projects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(Exception):
    """Raised when configuration is invalid."""


@dataclass
class AgentConfig:
    source: str = "./agent"
    model: str = "anthropic.claude-sonnet-4-20250514"
    description: str = ""
    memory: int = 512


@dataclass
class AppConfig:
    source: str = "./app"
    index: str = "index.html"
    error: str | None = None


@dataclass
class ApiConfig:
    prefix: str = "/api"


@dataclass
class ProjectConfig:
    name: str
    region: str = "us-east-1"
    agent: AgentConfig = field(default_factory=AgentConfig)
    app: AppConfig = field(default_factory=AppConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    project_dir: Path = field(default_factory=lambda: Path("."))


CONFIG_FILENAME = "three-stars.yml"


def load_config(
    project_dir: str | Path = ".",
    region_override: str | None = None,
) -> ProjectConfig:
    """Load and validate project configuration from three-stars.yml.

    Args:
        project_dir: Path to the project directory containing three-stars.yml.
        region_override: Override the region from config file.

    Returns:
        Validated ProjectConfig instance.

    Raises:
        ConfigError: If the config file is missing, malformed, or invalid.
    """
    project_path = Path(project_dir).resolve()
    config_path = project_path / CONFIG_FILENAME

    if not config_path.exists():
        raise ConfigError(
            f"Config file not found: {config_path}\n"
            f"Run 'three-stars init' to create a new project, "
            f"or ensure you're in the correct directory."
        )

    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {config_path}: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError(f"Config file must be a YAML mapping, got {type(raw).__name__}")

    # Validate required fields
    name = raw.get("name")
    if not name:
        raise ConfigError("'name' is required in three-stars.yml")
    if not isinstance(name, str):
        raise ConfigError(f"'name' must be a string, got {type(name).__name__}")

    # Build config
    region = region_override or raw.get("region", "us-east-1")

    agent_raw = raw.get("agent", {})
    agent = AgentConfig(
        source=agent_raw.get("source", "./agent"),
        model=agent_raw.get("model", AgentConfig.model),
        description=agent_raw.get("description", ""),
        memory=agent_raw.get("memory", 512),
    )

    app_raw = raw.get("app", {})
    app = AppConfig(
        source=app_raw.get("source", "./app"),
        index=app_raw.get("index", "index.html"),
        error=app_raw.get("error"),
    )

    api_raw = raw.get("api", {})
    api = ApiConfig(
        prefix=api_raw.get("prefix", "/api"),
    )

    config = ProjectConfig(
        name=name,
        region=region,
        agent=agent,
        app=app,
        api=api,
        project_dir=project_path,
    )

    _validate_config(config)
    return config


def _validate_config(config: ProjectConfig) -> None:
    """Validate config values after loading."""
    # Validate project name (used in AWS resource names)
    if not config.name.replace("-", "").replace("_", "").isalnum():
        raise ConfigError(
            f"Project name '{config.name}' contains invalid characters. "
            "Use only letters, numbers, hyphens, and underscores."
        )
    if len(config.name) > 50:
        raise ConfigError("Project name must be 50 characters or fewer.")

    # Validate paths exist
    agent_path = config.project_dir / config.agent.source
    if not agent_path.exists():
        raise ConfigError(
            f"Agent source directory not found: {agent_path}\n"
            f"Create it or update 'agent.source' in {CONFIG_FILENAME}."
        )

    app_path = config.project_dir / config.app.source
    if not app_path.exists():
        raise ConfigError(
            f"App source directory not found: {app_path}\n"
            f"Create it or update 'app.source' in {CONFIG_FILENAME}."
        )

    # Validate memory
    if config.agent.memory < 128:
        raise ConfigError("Agent memory must be at least 128 MB.")
    if config.agent.memory > 10240:
        raise ConfigError("Agent memory must be at most 10240 MB.")

    # Validate API prefix
    if not config.api.prefix.startswith("/"):
        raise ConfigError(f"API prefix must start with '/', got '{config.api.prefix}'")


def resolve_path(config: ProjectConfig, relative: str) -> Path:
    """Resolve a relative path against the project directory."""
    return (config.project_dir / relative).resolve()


def get_resource_prefix(config: ProjectConfig) -> str:
    """Generate a prefix for AWS resource names."""
    return f"three-stars-{config.name}"


def get_state_file_path(project_dir: str | Path = ".") -> Path:
    """Get the path to the deployment state file."""
    return Path(project_dir).resolve() / ".three-stars-state.json"
