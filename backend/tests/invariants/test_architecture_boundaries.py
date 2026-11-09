"""Tests for Architecture Boundary Enforcement Rules.

Enforces:
- app/core allowlist: stdlib, app.core.*, app.ports.*, langgraph, pydantic, typing_extensions.
- app/ports allowlist: stdlib, app.ports.*, pydantic, typing_extensions.
- app/adapters may import app.ports.* and vendor packages, but NEVER app.core.*.
- Only app/wiring.py may import app.core and app.adapters together.
- Resolves relative imports.
- Checks TYPE_CHECKING blocks.
- Bans dynamic imports (__import__, importlib.import_module) in core and ports.
- Bans environment reads (os.environ, os.getenv, from os import environ/getenv, aliased) in core.
"""

from pathlib import Path

from tests.invariants.boundary_checker import (
    check_code_boundaries,
    scan_directory_boundaries,
)


class TestBoundaryCheckerSelfTests:
    """Self-tests verifying the AST boundary checker detects every rule violation."""

    def test_violation_core_imports_adapters(self) -> None:
        code = "from app.adapters.models import MockModelProvider\n"
        violations = check_code_boundaries(code, "app/core/graph/nodes.py")
        assert len(violations) >= 1
        assert any("app.adapters" in v.message for v in violations)

    def test_violation_api_imports_adapters(self) -> None:
        code = "from app.adapters.models import OllamaModelProvider\n"
        violations = check_code_boundaries(code, "app/api/chat.py")
        assert len(violations) >= 1
        assert any("app.adapters" in v.message for v in violations)

    def test_violation_core_imports_db(self) -> None:
        code = "from app.db.models import Tenant\n"
        violations = check_code_boundaries(code, "app/core/retrieval/engine.py")
        assert len(violations) >= 1
        assert any("app.db" in v.message or "unlisted package" in v.message for v in violations)

    def test_violation_core_imports_unlisted_vendor(self) -> None:
        code = "import requests\n"
        violations = check_code_boundaries(code, "app/core/retrieval/engine.py")
        assert len(violations) >= 1
        assert any("requests" in v.message for v in violations)

    def test_violation_core_relative_import_into_adapters(self) -> None:
        code = "from ...adapters.storage import LocalStorage\n"
        violations = check_code_boundaries(code, "app/core/retrieval/store.py")
        assert len(violations) >= 1
        assert any("adapters" in v.message for v in violations)

    def test_violation_type_checking_block_import(self) -> None:
        code = (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from app.adapters.db import Session\n"
        )
        violations = check_code_boundaries(code, "app/core/graph/state.py")
        assert len(violations) >= 1
        assert any("app.adapters" in v.message for v in violations)

    def test_violation_dynamic_import_importlib(self) -> None:
        code = "import importlib\nmod = importlib.import_module('app.adapters.models')\n"
        violations = check_code_boundaries(code, "app/core/engine.py")
        assert len(violations) >= 1
        assert any("dynamic import" in v.message.lower() for v in violations)

    def test_violation_dynamic_import_builtin(self) -> None:
        code = "mod = __import__('docling')\n"
        violations = check_code_boundaries(code, "app/ports/parser.py")
        assert len(violations) >= 1
        assert any("dynamic import" in v.message.lower() for v in violations)

    def test_violation_core_reads_environment_environ(self) -> None:
        code = "import os\nkey = os.environ['API_KEY']\n"
        violations = check_code_boundaries(code, "app/core/guards/input.py")
        assert len(violations) >= 1
        assert any("environment read" in v.message.lower() for v in violations)

    def test_violation_core_reads_environment_getenv(self) -> None:
        code = "import os\nprof = os.getenv('APP_PROFILE')\n"
        violations = check_code_boundaries(code, "app/core/retrieval/ranker.py")
        assert len(violations) >= 1
        assert any("environment read" in v.message.lower() for v in violations)

    def test_violation_core_reads_environment_aliased_or_direct_import(self) -> None:
        code1 = "from os import getenv\nx = getenv('KEY')\n"
        violations1 = check_code_boundaries(code1, "app/core/guards/input.py")
        assert len(violations1) >= 1
        assert any("environment read" in v.message.lower() for v in violations1)

        code2 = "from os import environ\nx = environ['KEY']\n"
        violations2 = check_code_boundaries(code2, "app/core/guards/input.py")
        assert len(violations2) >= 1
        assert any("environment read" in v.message.lower() for v in violations2)

        code3 = "import os as o\nx = o.environ['KEY']\n"
        violations3 = check_code_boundaries(code3, "app/core/guards/input.py")
        assert len(violations3) >= 1
        assert any("environment read" in v.message.lower() for v in violations3)

    def test_violation_ports_imports_vendor_library(self) -> None:
        code = "import requests\n"
        violations = check_code_boundaries(code, "app/ports/http.py")
        assert len(violations) >= 1
        assert any("requests" in v.message for v in violations)

    def test_violation_ports_imports_core(self) -> None:
        code = "from app.core.graph import QuestionFlow\n"
        violations = check_code_boundaries(code, "app/ports/retriever.py")
        assert len(violations) >= 1
        assert any("app.core" in v.message for v in violations)

    def test_violation_adapters_imports_core(self) -> None:
        code = "from app.core.graph import GraphState\n"
        violations = check_code_boundaries(code, "app/adapters/models/ollama.py")
        assert len(violations) >= 1
        assert any("app.core" in v.message for v in violations)

    def test_violation_unauthorized_module_imports_both_core_and_adapters(self) -> None:
        code = (
            "from app.core.graph import GraphState\n"
            "from app.adapters.models import MockModelProvider\n"
        )
        violations = check_code_boundaries(code, "app/api/routes.py")
        assert len(violations) >= 1
        assert any("composition root" in v.message.lower() for v in violations)

    def test_valid_core_code_passes_checker(self) -> None:
        code = (
            "import json\n"
            "import sys\n"
            "from typing import List, Optional\n"
            "from typing_extensions import Annotated\n"
            "from pydantic import BaseModel\n"
            "from langgraph.graph import StateGraph\n"
            "from app.ports.models import ModelProvider\n"
            "from app.core.graph.state import GraphState\n"
        )
        violations = check_code_boundaries(code, "app/core/graph/nodes.py")
        assert violations == []

    def test_valid_ports_code_passes_checker(self) -> None:
        code = (
            "from abc import ABC, abstractmethod\n"
            "from typing import List, Dict, Any\n"
            "from typing_extensions import TypedDict\n"
            "from pydantic import BaseModel\n"
            "from app.ports.models import ModelRole\n"
        )
        violations = check_code_boundaries(code, "app/ports/retriever.py")
        assert violations == []

    def test_valid_adapters_code_passes_checker(self) -> None:
        code = (
            "import json\n"
            "import requests\n"
            "from typing import Any, Dict\n"
            "from app.ports.models import ModelProvider\n"
        )
        violations = check_code_boundaries(code, "app/adapters/models/ollama.py")
        assert violations == []


class TestRepoCodebaseScans:
    """Verifies that all actual source files in the repo satisfy architecture boundary rules."""

    def test_repo_scan_app_core_clean(self) -> None:
        backend_dir = Path(__file__).resolve().parents[2]
        core_dir = backend_dir / "app" / "core"
        if core_dir.exists():
            violations = scan_directory_boundaries(str(core_dir))
            msg = f"Found boundary violations in app/core: {[str(v) for v in violations]}"
            assert violations == [], msg

    def test_repo_scan_app_ports_clean(self) -> None:
        backend_dir = Path(__file__).resolve().parents[2]
        ports_dir = backend_dir / "app" / "ports"
        if ports_dir.exists():
            violations = scan_directory_boundaries(str(ports_dir))
            msg = f"Found boundary violations in app/ports: {[str(v) for v in violations]}"
            assert violations == [], msg

    def test_repo_scan_app_adapters_clean(self) -> None:
        backend_dir = Path(__file__).resolve().parents[2]
        adapters_dir = backend_dir / "app" / "adapters"
        if adapters_dir.exists():
            violations = scan_directory_boundaries(str(adapters_dir))
            msg = f"Found boundary violations in app/adapters: {[str(v) for v in violations]}"
            assert violations == [], msg

    def test_repo_scan_app_api_clean(self) -> None:
        backend_dir = Path(__file__).resolve().parents[2]
        api_dir = backend_dir / "app" / "api"
        if api_dir.exists():
            violations = scan_directory_boundaries(str(api_dir))
            msg = f"Found boundary violations in app/api: {[str(v) for v in violations]}"
            assert violations == [], msg

    def test_repo_scan_only_wiring_is_composition_root(self) -> None:
        backend_dir = Path(__file__).resolve().parents[2]
        app_dir = backend_dir / "app"
        violations = scan_directory_boundaries(str(app_dir))
        msg = f"Found unexpected violations in app: {[str(v) for v in violations]}"
        assert violations == [], msg

    def test_invariant_api_never_uses_owner_role_session(self) -> None:
        """API must never import or call owner-role session (cka_admin) from request paths."""
        import ast

        backend_dir = Path(__file__).resolve().parents[2]
        session_file = backend_dir / "app" / "db" / "session.py"
        session_code = session_file.read_text(encoding="utf-8")
        assert "get_admin_engine" not in session_code, (
            "get_admin_engine must not exist in session.py"
        )
        assert "get_admin_session_factory" not in session_code, (
            "get_admin_session_factory must not exist in session.py"
        )

        api_dir = backend_dir / "app" / "api"
        for py_file in api_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.names:
                    for name in node.names:
                        assert name.name not in (
                            "get_admin_engine",
                            "get_admin_session_factory",
                            "admin_session",
                        ), f"{py_file.name} imports forbidden owner session symbol {name.name}"
                if isinstance(node, ast.Constant) and node.value == "cka_admin":
                    raise AssertionError(
                        f"{py_file.name} contains forbidden 'cka_admin' literal in request path"
                    )

    def test_no_route_or_adapter_branches_on_golden_filenames_or_hardcoded_extractions(
        self,
    ) -> None:
        """Fail if any route, core module, or adapter in backend/app branches on golden
        document filenames or contains hardcoded fake extraction/citation values.
        (Only app/core/safety_lock.py is allowed to list golden filenames for the cloud
        SHA-256 synthetic data lock.)
        """
        backend_dir = Path(__file__).resolve().parents[2]
        app_dir = backend_dir / "app"

        forbidden_substrings = (
            # Golden document filename stems and extraction branch tokens
            "skewed_fax",
            "faint_fax",
            "invalid_npi",
            "unreadable_field",
            "missing_field",
            "in lower_name",
            "front_desk_policy",
            "prior_auth_guide",
            "accepted_plans.csv",
            "pa_turnaround_times",
            "fee_schedule",
            "patient_intake_form",
            "compliance_notice",
            # Hardcoded fake extraction or citation values
            "Amoxicillin 500mg",
            "Amoxicillin 5oOmg",
            "Amoxillin",
            "MRI Lumbar Spine",
            "MRN-4412-A",
            "Alex Morgan",
            "Evelyn Reed",
            "1982736451",
            "Clinic Policy 2023",
            "Clinic Policy 2024",
            "Apex Health Insurance",
        )

        violations: list[str] = []
        for py_file in sorted(app_dir.rglob("*.py")):
            rel_path = py_file.relative_to(backend_dir).as_posix()
            if rel_path == "app/core/safety_lock.py":
                continue
            text = py_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                for token in forbidden_substrings:
                    if token in line:
                        violations.append(f"{rel_path}:{lineno}: forbidden token {token!r}")

        assert violations == [], (
            "Found golden filename branching or hardcoded document/field content in backend/app:\n"
            + "\n".join(violations)
        )

    def test_dockerfiles_and_compose_entrypoints_and_drivers_are_valid(self) -> None:
        """Verify backend/Dockerfile and web/Dockerfile exist and docker-compose.yml uses valid
        entrypoints (app.api.app:create_app --factory, app.worker.app), psycopg, and API_ORIGIN.
        """
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        backend_dockerfile = repo_root / "backend" / "Dockerfile"
        web_dockerfile = repo_root / "web" / "Dockerfile"
        compose_file = repo_root / "docker-compose.yml"

        assert backend_dockerfile.is_file(), "backend/Dockerfile must exist"
        assert web_dockerfile.is_file(), "web/Dockerfile must exist"
        assert compose_file.is_file(), "docker-compose.yml must exist"

        compose_text = compose_file.read_text(encoding="utf-8")
        assert "app.api.app:create_app" in compose_text
        assert "--factory" in compose_text
        assert "app.api.main:app" not in compose_text
        assert "app.worker.app" in compose_text
        assert "app.adapters.queue.worker" not in compose_text
        assert "postgresql+psycopg://" in compose_text
        assert "postgresql+asyncpg://" not in compose_text
        assert "API_ORIGIN" in compose_text
