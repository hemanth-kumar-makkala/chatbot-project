"""Environment-loader safety regressions."""

from pathlib import Path

import pytest

from app.db.env_utils import load_dotenv


def test_skip_dotenv_environment_flag_returns_before_any_file_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HERMES_SKIP_DOTENV", "1")

    def fail_if_file_checked(_self: Path) -> bool:
        raise AssertionError("dotenv lookup should be skipped entirely")

    monkeypatch.setattr(Path, "is_file", fail_if_file_checked)

    load_dotenv()
