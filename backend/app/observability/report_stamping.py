"""Report stamping utility for evaluation, benchmark, and smoke reports."""

from __future__ import annotations

import os
import platform
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def get_git_commit(repo_root: Path | None = None) -> str:
    """Read HEAD git commit directly from .git files without running git executable."""
    try:
        root = repo_root or Path(__file__).resolve().parent.parent.parent.parent
        git_dir = root / ".git"
        if not git_dir.exists():
            return "unavailable"
        if git_dir.is_file():
            # git worktree / submodule
            content = git_dir.read_text(encoding="utf-8").strip()
            if content.startswith("gitdir:"):
                git_dir = Path(content.split("gitdir:", 1)[1].strip())
        head_file = git_dir / "HEAD"
        if not head_file.exists():
            return "unavailable"
        head_ref = head_file.read_text(encoding="utf-8").strip()
        if head_ref.startswith("ref:"):
            ref_path = git_dir / head_ref.split("ref:", 1)[1].strip()
            if ref_path.exists():
                return ref_path.read_text(encoding="utf-8").strip()
            # check packed-refs
            packed_refs = git_dir / "packed-refs"
            if packed_refs.exists():
                ref_target = head_ref.split("ref:", 1)[1].strip()
                for line in packed_refs.read_text(encoding="utf-8").splitlines():
                    if line.startswith("#"):
                        continue
                    parts = line.strip().split()
                    if len(parts) == 2 and parts[1] == ref_target:
                        return parts[0]
            return "unavailable"
        return head_ref
    except Exception:
        return "unavailable"


def get_machine_info() -> dict[str, str]:
    """Retrieve machine hostname, platform, and GPU name."""
    machine_name = platform.node() or socket.gethostname() or "unavailable"
    gpu_name = os.getenv("GPU_NAME", "unavailable")
    if gpu_name == "unavailable":
        try:
            import torch

            if torch.cuda.is_available():
                gpu_name = str(torch.cuda.get_device_name(0))
        except Exception:
            pass
    return {
        "machine_name": machine_name,
        "gpu_name": gpu_name,
        "platform": platform.platform(),
    }


def create_report_stamp(
    profile: str,
    models_by_role: dict[str, str],
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Create comprehensive telemetry metadata stamp for reports."""
    mach = get_machine_info()
    return {
        "profile": profile,
        "models": dict(models_by_role),
        "machine_name": mach["machine_name"],
        "gpu_name": mach["gpu_name"],
        "platform": mach["platform"],
        "timestamp": datetime.now(UTC).isoformat(),
        "git_commit": get_git_commit(repo_root=repo_root),
    }
