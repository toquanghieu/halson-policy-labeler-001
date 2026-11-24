"""The ports-and-adapters rules, checked on the source so a later change cannot quietly break them.

    infra/<component>/port.py          the contract (a typing.Protocol); standard library only
    infra/<component>/<name>_adapter.py one implementation each
    bootstrap.py                       the only place that picks adapters (plus the LLM registry)

The domain (labeling/) may use infrastructure only through ports. Use cases (services/) may also
use the repository, the metrics module and the provider catalogue, never an adapter. The API and
the worker go through services and never touch an adapter.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from labeler.config import REPO_ROOT

CORE = REPO_ROOT / "core" / "labeler"
INFRA = CORE / "infra"
PORT_PACKAGES = ["queue", "storage", "secrets", "llm"]
STDLIB = set(sys.stdlib_module_names) | {"__future__"}


def imports(path: Path) -> list[str]:
    """Every module imported by `path`, including imports inside functions."""
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append(node.module)
    return out


def py_files(*dirs: Path) -> list[Path]:
    return [p for d in dirs for p in d.rglob("*.py") if "__pycache__" not in p.parts]


def test_every_port_package_has_a_port_and_adapters():
    for name in PORT_PACKAGES:
        pkg = INFRA / name
        assert (pkg / "port.py").is_file(), f"{name}: no port.py"
        assert list(pkg.glob("*_adapter.py")), f"{name}: no *_adapter.py"


def test_ports_import_only_the_standard_library():
    for name in PORT_PACKAGES:
        for mod in imports(INFRA / name / "port.py"):
            top = mod.split(".")[0]
            assert top in STDLIB or mod == "labeler.errors", f"{name}/port.py imports {mod}"


def test_the_domain_uses_infrastructure_only_through_ports():
    for path in py_files(CORE / "labeling"):
        for mod in imports(path):
            if mod.startswith("labeler.infra"):
                assert mod.endswith(".port"), f"{path.relative_to(REPO_ROOT)} imports {mod}"


def test_use_cases_never_import_an_adapter():
    allowed = {"labeler.infra.db", "labeler.infra.telemetry", "labeler.infra.llm.registry"}
    for path in py_files(CORE / "services"):
        for mod in imports(path):
            if mod.startswith("labeler.infra"):
                assert mod.endswith(".port") or mod in allowed, f"{path.relative_to(REPO_ROOT)} imports {mod}"


def test_only_the_composition_root_picks_adapters():
    pickers = {CORE / "bootstrap.py", INFRA / "llm" / "registry.py", INFRA / "llm" / "slm_adapter.py"}
    for path in py_files(CORE, REPO_ROOT / "backend", REPO_ROOT / "worker"):
        if path in pickers:
            continue
        adapters = [m for m in imports(path) if m.endswith("_adapter")]
        assert not adapters, f"{path.relative_to(REPO_ROOT)} imports adapters {adapters}"
