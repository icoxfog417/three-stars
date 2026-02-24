"""Project initialization for three-stars."""

from __future__ import annotations

import shutil
from pathlib import Path

from rich.console import Console

console = Console()

TEMPLATES_DIR = Path(__file__).parent.parent / "three_stars_templates"


def run_init(name: str, template: str = "starter", base_dir: Path | None = None) -> None:
    """Scaffold a new three-stars project.

    Args:
        name: Project name (also used as directory name).
        template: Template to use.
        base_dir: Parent directory where the project folder will be created.
            Defaults to the current working directory.
    """
    if base_dir is None:
        base_dir = Path.cwd()
    target_dir = Path(base_dir) / name

    if target_dir.exists():
        raise FileExistsError(f"Directory '{name}' already exists.")

    template_dir = TEMPLATES_DIR / template
    if not template_dir.exists():
        available = ", ".join(t.name for t in TEMPLATES_DIR.iterdir() if t.is_dir())
        raise FileNotFoundError(
            f"Template '{template}' not found. Available templates: {available}"
        )

    # Copy template
    shutil.copytree(template_dir, target_dir)

    # Update config with project name
    config_path = target_dir / "three-stars.yml"
    if config_path.exists():
        content = config_path.read_text()
        content = content.replace("my-ai-app", name)
        config_path.write_text(content)

    console.print(f"\n[bold green]Created project:[/bold green] {name}/")
    console.print()
    console.print("Project structure:")
    _print_tree(target_dir, prefix="  ")
    console.print()
    console.print("Next steps:")
    console.print(f"  cd {name}")
    console.print("  # Edit agent/agent.py with your agent logic")
    console.print("  # Edit app/index.html with your frontend")
    console.print("  sss deploy")


def _print_tree(directory: Path, prefix: str = "", is_last: bool = True) -> None:
    """Print a directory tree."""
    entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name))
    for i, entry in enumerate(entries):
        is_entry_last = i == len(entries) - 1
        connector = "└── " if is_entry_last else "├── "
        console.print(f"{prefix}{connector}{entry.name}")
        if entry.is_dir():
            extension = "    " if is_entry_last else "│   "
            _print_tree(entry, prefix + extension, is_entry_last)
