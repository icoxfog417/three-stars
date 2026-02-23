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
    model: str = "us.anthropic.claude-sonnet-4-6"
    description: str = ""
    env_vars: dict[str, str] = field(default_factory=dict)


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
    tags: dict[str, str] = field(default_factory=dict)
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
            f"Run 'sss init' to create a new project, "
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
    env_vars_raw = agent_raw.get("env_vars", {})
    if not isinstance(env_vars_raw, dict):
        raise ConfigError(f"'agent.env_vars' must be a mapping, got {type(env_vars_raw).__name__}")
    for k, v in env_vars_raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ConfigError(f"agent.env_vars key and value must be strings, got {k!r}: {v!r}")

    agent = AgentConfig(
        source=agent_raw.get("source", "./agent"),
        model=agent_raw.get("model", AgentConfig.model),
        description=agent_raw.get("description", ""),
        env_vars=env_vars_raw,
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

    # User-defined tags (merged with standard tags later)
    user_tags = raw.get("tags", {})
    if not isinstance(user_tags, dict):
        raise ConfigError(f"'tags' must be a mapping, got {type(user_tags).__name__}")
    for k, v in user_tags.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ConfigError(f"Tag key and value must be strings, got {k!r}: {v!r}")

    config = ProjectConfig(
        name=name,
        region=region,
        agent=agent,
        app=app,
        api=api,
        tags=user_tags,
        project_dir=project_path,
    )

    _validate_config(config)
    return config


def _validate_config(config: ProjectConfig) -> None:
    """Validate config values after loading."""
    # Validate project name (used in AWS resource names, including S3 buckets)
    if not config.name.replace("-", "").isalnum():
        raise ConfigError(
            f"Project name '{config.name}' contains invalid characters. "
            "Use only lowercase letters, numbers, and hyphens."
        )
    if config.name != config.name.lower():
        raise ConfigError(
            f"Project name '{config.name}' must be lowercase. Try: '{config.name.lower()}'"
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

    # Validate API prefix
    if not config.api.prefix.startswith("/"):
        raise ConfigError(f"API prefix must start with '/', got '{config.api.prefix}'")


def resolve_path(config: ProjectConfig, relative: str) -> Path:
    """Resolve a relative path against the project directory."""
    return (config.project_dir / relative).resolve()


def get_resource_tags(config: ProjectConfig) -> dict[str, str]:
    """Compute the merged tag set for AWS resources.

    Standard tags are always applied. User-defined tags from config
    are merged in, but standard tags take precedence.
    """
    standard = {
        "three-stars:project": config.name,
        "three-stars:managed-by": "three-stars",
        "three-stars:region": config.region,
    }
    merged = {**config.tags, **standard}
    return merged


def tags_to_aws(tags: dict[str, str]) -> list[dict[str, str]]:
    """Convert a tags dict to the AWS Tags format: [{"Key": k, "Value": v}]."""
    return [{"Key": k, "Value": v} for k, v in tags.items()]
