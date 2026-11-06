"""Environment variable loader for database configuration.

Loads .env into os.environ if present without overwriting existing environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(explicit_path: str | Path | None = None) -> None:
    """Load key-value pairs from .env into os.environ unless explicitly disabled."""
    if os.environ.get("HERMES_SKIP_DOTENV") == "1":
        return

    candidates: list[Path]
    if explicit_path is not None:
        candidates = [Path(explicit_path)]
    else:
        current_file = Path(__file__).resolve()
        candidates = [
            Path(".env"),
            Path("backend/.env"),
            *[p / ".env" for p in current_file.parents],
        ]

    for candidate in candidates:
        if candidate.is_file():
            try:
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = val
                break
            except OSError:
                continue
