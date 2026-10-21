"""Adapter for loading YAML prompt registry outside app.core.

Adheres to Clean Architecture:
- Parsing YAML requires PyYAML (vendor dependency), which resides in app/adapters.
- Core remains pure Python (stdlib + pydantic) and receives parsed registry dictionary
  via injection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_yaml_prompt_registry(registry_path: Path | None = None) -> dict[str, Any]:
    """Load and parse prompts/registry.yaml into a dictionary."""
    if registry_path and registry_path.is_file():
        candidates = [registry_path]
    else:
        candidates = [
            Path(__file__).resolve().parent.parent.parent.parent / "prompts" / "registry.yaml",
            Path.cwd() / "prompts" / "registry.yaml",
            Path.cwd().parent / "prompts" / "registry.yaml",
        ]

    for path in candidates:
        if path.is_file():
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return data or {}

    raise FileNotFoundError("prompts/registry.yaml not found in candidate paths")
