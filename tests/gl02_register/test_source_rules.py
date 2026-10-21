"""AST scan of the obligation package for constructs the source rules forbid."""

from __future__ import annotations

import ast
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parents[2] / "src" / "idis" / "methodology" / "obligations"
_REGISTRY_NAME = "import_fdd_formula_policy_v2_registry"
_MODULE_LABELS = (
    ("idis.api", "idis.api"),
    ("idis.persistence", "idis.persistence"),
    ("idis.services", "idis.services"),
    ("subprocess", "subprocess"),
    ("random", "random"),
)
_LABELS = frozenset(
    {
        "print",
        "os.environ",
        "os.getenv",
        "subprocess",
        "time.sleep",
        "time.time",
        "datetime.now",
        "random",
        "idis.api",
        "idis.persistence",
        "idis.services",
        _REGISTRY_NAME,
    }
)
_PROBE = """\
import os
import random
import subprocess
import time
import datetime
import idis.api
import idis.persistence
import idis.services


def probe() -> None:
    print("fictional")
    os.environ["FICTIONAL"]
    os.getenv("FICTIONAL")
    subprocess.run(["fictional"])
    time.sleep(0)
    time.time()
    datetime.datetime.now()
    random.random()


# import_fdd_formula_policy_v2_registry
"""


def test_obligation_sources_avoid_forbidden_constructs() -> None:
    failures: list[str] = []
    for path in sorted(_PACKAGE.glob("*.py")):
        hits = scan_source(path.read_text(encoding="utf-8"))
        if hits:
            failures.append(f"{path.name}: {', '.join(sorted(hits))}")
    assert failures == []


def test_scanner_rejects_each_forbidden_construct(tmp_path: Path) -> None:
    path = tmp_path / "probe.py"
    path.write_text(_PROBE, encoding="utf-8")
    assert scan_source(path.read_text(encoding="utf-8")) == _LABELS


def scan_source(source: str) -> set[str]:
    """Return the forbidden constructs present in one Python source."""
    found: set[str] = set()
    if _REGISTRY_NAME in source:
        found.add(_REGISTRY_NAME)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        found.update(_import_labels(node))
        found.update(_call_labels(node))
        if isinstance(node, ast.Attribute):
            dotted = _dotted(node)
            if dotted == "os.environ" or dotted.startswith("os.environ."):
                found.add("os.environ")
    return found


def _import_labels(node: ast.AST) -> set[str]:
    found: set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            found.update(_module_labels(alias.name))
        return found
    if not isinstance(node, ast.ImportFrom) or node.module is None:
        return found
    found.update(_module_labels(node.module))
    if node.module == "idis":
        for alias in node.names:
            if alias.name in {"api", "persistence", "services"}:
                found.add(f"idis.{alias.name}")
    if node.module == "os":
        for alias in node.names:
            if alias.name in {"environ", "getenv"}:
                found.add(f"os.{alias.name}")
    if node.module == "time":
        for alias in node.names:
            if alias.name in {"sleep", "time"}:
                found.add(f"time.{alias.name}")
    return found


def _module_labels(name: str) -> set[str]:
    found: set[str] = set()
    for prefix, label in _MODULE_LABELS:
        if name == prefix or name.startswith(f"{prefix}."):
            found.add(label)
    return found


def _call_labels(node: ast.AST) -> set[str]:
    if not isinstance(node, ast.Call):
        return set()
    dotted = _dotted(node.func)
    found: set[str] = set()
    if dotted == "print":
        found.add("print")
    if dotted == "os.getenv":
        found.add("os.getenv")
    if dotted in {"time.sleep", "time.time"}:
        found.add(dotted)
    if dotted == "datetime.now" or (dotted.endswith(".now") and "datetime" in dotted.split(".")):
        found.add("datetime.now")
    if dotted == "random" or dotted.startswith("random."):
        found.add("random")
    if dotted == "subprocess" or dotted.startswith("subprocess."):
        found.add("subprocess")
    return found


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        if base == "":
            return node.attr
        return f"{base}.{node.attr}"
    return ""
