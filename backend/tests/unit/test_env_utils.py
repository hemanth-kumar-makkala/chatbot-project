"""Environment-loader safety regressions."""

from pathlib import Path

import pytest

from app.db.env_utils import load_dotenv


def test_load_dotenv_reads_explicit_path_without_overwriting_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Load explicit dotenv values while preserving existing environment settings."""
    monkeypatch.setenv("DOTENV_TEST_EXISTING", "environment-value")
    dotenv_file = tmp_path / "synthetic.env"
    dotenv_file.write_text(
        "DOTENV_TEST_EXISTING=file-value\nDOTENV_TEST_NEW=loaded-value\n",
        encoding="utf-8",
    )

    load_dotenv(dotenv_file)

    import os

    assert os.environ["DOTENV_TEST_EXISTING"] == "environment-value"
    assert os.environ["DOTENV_TEST_NEW"] == "loaded-value"
    monkeypatch.delenv("DOTENV_TEST_NEW")
