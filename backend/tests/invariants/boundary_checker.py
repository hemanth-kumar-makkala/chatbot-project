"""Architecture Boundary Checker for AST Analysis.

Enforces:
1. Allowlist-based import checking for app/core, app/ports, app/adapters.
2. Relative import resolution.
3. Checking imports inside TYPE_CHECKING blocks.
4. Banning dynamic imports (__import__, importlib.import_module).
5. Banning environment variable reads in app/core (os.environ, os.getenv, from os import ...).
6. Enforcing single composition root (app/wiring.py).
"""

import ast
import sys
from pathlib import Path

# Python standard library modules
STDLIB_MODULES: set[str] = set(sys.stdlib_module_names) | {
    "sys",
    "os",
    "typing",
    "json",
    "math",
    "re",
    "dataclasses",
    "abc",
    "functools",
    "itertools",
    "collections",
    "pathlib",
    "datetime",
    "uuid",
    "logging",
    "hashlib",
    "io",
    "copy",
    "enum",
    "time",
    "contextlib",
    "types",
    "warnings",
    "traceback",
    "inspect",
    "asyncio",
}

# Explicit package allowlists
CORE_ALLOWED_ROOTS: set[str] = {
    "app.core",
    "app.ports",
    "langgraph",
    "pydantic",
    "typing_extensions",
}

PORTS_ALLOWED_ROOTS: set[str] = {
    "app.ports",
    "pydantic",
    "typing_extensions",
}


class BoundaryViolation:
    """Represents an architectural boundary violation."""

    def __init__(self, file_path: str, line_number: int, violation_type: str, message: str) -> None:
        self.file_path = file_path
        self.line_number = line_number
        self.violation_type = violation_type
        self.message = message

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line_number} [{self.violation_type}] {self.message}"

    def __repr__(self) -> str:
        return self.__str__()


def _normalize_module_path(path_str: str) -> str:
    """Normalize file path to POSIX relative form e.g. app/core/graph/nodes.py."""
    posix_path = path_str.replace("\\", "/")
    if "backend/" in posix_path:
        posix_path = posix_path.split("backend/", 1)[1]
    if posix_path.startswith("./"):
        posix_path = posix_path[2:]
    return posix_path


def _resolve_relative_import(module_path: str, level: int, import_name: str | None) -> str:
    """Resolve a relative import to its fully qualified dotted module name."""
    parts = module_path.split("/")
    pkg_parts = parts[:-1]
    trim = level - 1
    if trim > 0:
        pkg_parts = pkg_parts[:-trim] if trim <= len(pkg_parts) else []
    base_pkg = ".".join(pkg_parts)
    if import_name:
        return f"{base_pkg}.{import_name}" if base_pkg else import_name
    return base_pkg


def _matches_any_prefix(module_name: str, allowed_roots: set[str]) -> bool:
    """Check if module_name is or starts with any allowed root."""
    return any(module_name == root or module_name.startswith(f"{root}.") for root in allowed_roots)


def _is_stdlib(module_name: str) -> bool:
    """Check if root package belongs to Python standard library."""
    root_pkg = module_name.split(".")[0]
    return root_pkg in STDLIB_MODULES


class _BoundaryVisitor(ast.NodeVisitor):
    def __init__(self, code: str, module_path: str) -> None:
        self.code = code
        self.module_path = _normalize_module_path(module_path)
        self.violations: list[BoundaryViolation] = []
        self.imports_core: bool = False
        self.imports_adapters: bool = False
        self.os_aliases: set[str] = {"os"}
        self.env_direct_functions: set[str] = set()

    def _add_violation(self, node: ast.AST, violation_type: str, message: str) -> None:
        lineno = getattr(node, "lineno", 1)
        self.violations.append(
            BoundaryViolation(
                file_path=self.module_path,
                line_number=lineno,
                violation_type=violation_type,
                message=message,
            )
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            imported = alias.name
            asname = alias.asname or alias.name
            if imported == "os":
                self.os_aliases.add(asname)
            self._check_import_target(node, imported)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module
        level = node.level

        if level > 0:
            resolved_mod = _resolve_relative_import(self.module_path, level, module)
        else:
            resolved_mod = module or ""

        # Check for direct environment imports in app/core
        if self._is_core_module() and (module == "os" or resolved_mod == "os"):
            for alias in node.names:
                name = alias.name
                asname = alias.asname or name
                if name in {"environ", "getenv"}:
                    self.env_direct_functions.add(asname)
                    msg = (
                        f"Direct environment read import 'from os import {name}' "
                        f"is forbidden in app/core/"
                    )
                    self._add_violation(node, "FORBIDDEN_ENV_READ", msg)

        for alias in node.names:
            full_name = f"{resolved_mod}.{alias.name}" if resolved_mod else alias.name
            self._check_import_target(node, full_name, base_mod=resolved_mod)

        self.generic_visit(node)

    def _is_app_module(self) -> bool:
        return self.module_path.startswith("app/") or "/app/" in self.module_path

    def _is_core_module(self) -> bool:
        return self.module_path.startswith("app/core/") or "/app/core/" in self.module_path

    def _is_ports_module(self) -> bool:
        return self.module_path.startswith("app/ports/") or "/app/ports/" in self.module_path

    def _is_adapters_module(self) -> bool:
        return self.module_path.startswith("app/adapters/") or "/app/adapters/" in self.module_path

    def _is_composition_root(self) -> bool:
        return self.module_path == "app/wiring.py" or self.module_path.endswith("/app/wiring.py")

    def _check_import_target(
        self, node: ast.AST, full_target: str, base_mod: str | None = None
    ) -> None:
        root_pkg = full_target.split(".")[0]
        check_target = base_mod if (base_mod and base_mod.startswith("app.")) else full_target

        if check_target.startswith("app.core") or full_target.startswith("app.core"):
            self.imports_core = True
        if check_target.startswith("app.adapters") or full_target.startswith("app.adapters"):
            self.imports_adapters = True

        # Rule: No module in app/ outside app/adapters and app/wiring.py may import app.adapters
        if (
            (check_target.startswith("app.adapters") or full_target.startswith("app.adapters"))
            and self._is_app_module()
            and not self._is_adapters_module()
            and not self._is_composition_root()
        ):
            msg = (
                f"Only app/wiring.py may import app.adapters from outside app/adapters/ "
                f"(attempted: '{full_target}')"
            )
            self._add_violation(node, "FORBIDDEN_ADAPTERS_IMPORT", msg)

        if self._is_core_module():
            if _is_stdlib(root_pkg):
                return
            if _matches_any_prefix(full_target, CORE_ALLOWED_ROOTS):
                return
            if base_mod and _matches_any_prefix(base_mod, CORE_ALLOWED_ROOTS):
                return
            msg = (
                f"app/core may not import unlisted package '{full_target}'. "
                f"Allowed: stdlib, app.core.*, app.ports.*, langgraph, pydantic, typing_extensions"
            )
            self._add_violation(node, "FORBIDDEN_IMPORT_IN_CORE", msg)

        elif self._is_ports_module():
            if _is_stdlib(root_pkg):
                return
            if _matches_any_prefix(full_target, PORTS_ALLOWED_ROOTS):
                return
            if base_mod and _matches_any_prefix(base_mod, PORTS_ALLOWED_ROOTS):
                return
            msg = (
                f"app/ports may not import unlisted package '{full_target}'. "
                f"Allowed: stdlib, app.ports.*, pydantic, typing_extensions"
            )
            self._add_violation(node, "FORBIDDEN_IMPORT_IN_PORTS", msg)

        elif self._is_adapters_module():
            if full_target.startswith("app.core") or (base_mod and base_mod.startswith("app.core")):
                msg = f"app/adapters may never import app.core (attempted: '{full_target}')"
                self._add_violation(node, "FORBIDDEN_CORE_IMPORT_IN_ADAPTERS", msg)

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_core_module() or self._is_ports_module():
            if isinstance(node.func, ast.Name):
                if node.func.id == "__import__":
                    self._add_violation(
                        node,
                        "FORBIDDEN_DYNAMIC_IMPORT",
                        "Dynamic import '__import__' is banned in app/core and app/ports",
                    )
                elif node.func.id in self.env_direct_functions and self._is_core_module():
                    self._add_violation(
                        node,
                        "FORBIDDEN_ENV_READ",
                        f"Direct environment read via '{node.func.id}()' is banned in app/core",
                    )
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib"
                and node.func.attr == "import_module"
            ):
                msg = "Dynamic import 'importlib.import_module' is banned in app/core and app/ports"
                self._add_violation(node, "FORBIDDEN_DYNAMIC_IMPORT", msg)

            if (
                self._is_core_module()
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in self.os_aliases
                and node.func.attr == "getenv"
            ):
                msg = f"Environment read via '{node.func.value.id}.getenv()' is banned in app/core"
                self._add_violation(node, "FORBIDDEN_ENV_READ", msg)

        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            self._is_core_module()
            and isinstance(node.value, ast.Name)
            and node.value.id in self.os_aliases
            and node.attr == "environ"
        ):
            msg = f"Environment read via '{node.value.id}.environ' is banned in app/core"
            self._add_violation(node, "FORBIDDEN_ENV_READ", msg)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if (
            self._is_core_module()
            and isinstance(node.value, ast.Name)
            and node.value.id in self.env_direct_functions
        ):
            msg = f"Environment read via '{node.value.id}' is banned in app/core"
            self._add_violation(node, "FORBIDDEN_ENV_READ", msg)
        self.generic_visit(node)


def check_code_boundaries(code: str, module_path: str) -> list[BoundaryViolation]:
    """Check code string for boundary violations given its module path."""
    parsed = ast.parse(code, filename=module_path)
    visitor = _BoundaryVisitor(code, module_path)
    visitor.visit(parsed)

    if visitor.imports_core and visitor.imports_adapters and not visitor._is_composition_root():
        msg = (
            "Only app/wiring.py may act as composition root "
            "importing both app.core and app.adapters"
        )
        visitor.violations.append(
            BoundaryViolation(
                file_path=visitor.module_path,
                line_number=1,
                violation_type="UNAUTHORIZED_COMPOSITION_ROOT",
                message=msg,
            )
        )

    return visitor.violations


def check_file_boundaries(file_path: str) -> list[BoundaryViolation]:
    """Check a file on disk for boundary violations."""
    path = Path(file_path)
    if not path.exists() or path.suffix != ".py":
        return []
    code = path.read_text(encoding="utf-8")
    return check_code_boundaries(code, str(path))


def scan_directory_boundaries(directory_path: str) -> list[BoundaryViolation]:
    """Scan all python files in directory for boundary violations."""
    path = Path(directory_path)
    if not path.exists():
        return []

    violations: list[BoundaryViolation] = []
    for py_file in path.rglob("*.py"):
        if ".venv" in py_file.parts or "__pycache__" in py_file.parts:
            continue
        violations.extend(check_file_boundaries(str(py_file)))
    return violations
