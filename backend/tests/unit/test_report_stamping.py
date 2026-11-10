"""Unit tests for report stamping utility."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.observability.report_stamping import create_report_stamp, get_git_commit


def test_get_git_commit_reads_from_git_directory(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    head_file = git_dir / "HEAD"
    head_file.write_text("ref: refs/heads/main\n", encoding="utf-8")
    ref_file = git_dir / "refs" / "heads" / "main"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text("0123456789abcdef0123456789abcdef01234567\n", encoding="utf-8")

    commit = get_git_commit(repo_root=tmp_path)
    assert commit == "0123456789abcdef0123456789abcdef01234567"  # pragma: allowlist secret


def test_get_git_commit_returns_unavailable_when_git_missing(tmp_path: Path) -> None:
    commit = get_git_commit(repo_root=tmp_path)
    assert commit == "unavailable"


def test_create_report_stamp_captures_all_metadata(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("fedcba9876543210fedcba9876543210fedcba98\n", encoding="utf-8")

    models = {
        "router": "nvidia/nemotron-3.5-super-vl-120b-a12b",
        "answer": "nvidia/nemotron-3.5-super-vl-120b-a12b",
        "vision": "nvidia/nemotron-3.5-super-vl-120b-a12b",
        "extract": "nvidia/nemotron-3.5-super-vl-120b-a12b",
        "judge": "mistralai/mistral-large-4-1t-a52b",
    }

    with patch.dict("os.environ", {"GPU_NAME": "NVIDIA GeForce RTX 3050"}):
        stamp = create_report_stamp(
            profile="desktop-api",
            models_by_role=models,
            repo_root=tmp_path,
        )

    assert stamp["profile"] == "desktop-api"
    assert stamp["models"] == models
    expected_hash = "fedcba9876543210fedcba9876543210fedcba98"  # pragma: allowlist secret
    assert stamp["git_commit"] == expected_hash
    assert stamp["gpu_name"] == "NVIDIA GeForce RTX 3050"
    assert "machine_name" in stamp and stamp["machine_name"]
    assert "timestamp" in stamp and stamp["timestamp"]
    assert "platform" in stamp and stamp["platform"]
