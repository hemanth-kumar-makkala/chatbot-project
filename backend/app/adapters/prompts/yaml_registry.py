"""Adapter for reading and validating prompts from prompts/registry.yaml.

Adapters are permitted to import PyYAML and access filesystem resources.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


def find_registry_path() -> Path:
    """Locate prompts/registry.yaml across repo structures."""
    candidates = (
        Path(__file__).resolve().parent.parent.parent.parent / "prompts" / "registry.yaml",
        Path("prompts/registry.yaml"),
        Path("../prompts/registry.yaml"),
    )
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError("prompts/registry.yaml not found")


@lru_cache(maxsize=1)
def load_prompt_registry() -> dict[str, Any]:
    """Load and cache the central prompt registry YAML."""
    path = find_registry_path()
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "prompts" not in data:
        raise ValueError("Invalid prompt registry structure")
    return data
