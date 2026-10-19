"""The closed-list count step, extracted from ci.yml, rejects five JUnit shapes."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml
from _pytest.junitxml import mangle_test_address

from tests._linux_poppler_gate import LINUX_POPPLER_DATABASE_NODES, collected_linux_poppler_node_ids

_REPO = Path(__file__).resolve().parents[1]
_XML_NAME = "c3_linux_poppler_pytest.xml"
_DATABASE_XML_NAME = "c3_linux_poppler_database_pytest.xml"
_SUFFIX = " <- tests/_linux_poppler_gate.py"


def _count_step_python() -> str:
    workflow = yaml.safe_load((_REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["postgres-integration"]["steps"]
    step = next(item for item in steps if item.get("id") == "c3-linux-poppler-count")
    script = str(step["run"])
    marker = ".venv/bin/python - <<'PY'\n"
    start = script.index(marker) + len(marker)
    return script[start : script.index("\nPY\n", start)]


def _partition(node_ids: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    database_keys = set(LINUX_POPPLER_DATABASE_NODES)
    database: list[str] = []
    no_database: list[str] = []
    for node_id in node_ids:
        head = node_id.split("[", 1)[0]
        function = head.rsplit("::", 1)[-1]
        module = node_id.split("::", 1)[0].replace("\\", "/")
        if (module, function) in database_keys:
            database.append(node_id)
        else:
            no_database.append(node_id)
    return tuple(no_database), tuple(database)


def _write_suite(
    path: Path,
    node_ids: tuple[str, ...],
    *,
    tests: int,
    skipped: int,
    not_executed: bool,
    suffix_on_first: bool,
) -> None:
    suite = ET.Element(
        "testsuite",
        {
            "tests": str(tests),
            "failures": "0",
            "errors": "0",
            "skipped": str(skipped),
        },
    )
    for index, node_id in enumerate(node_ids):
        names = mangle_test_address(node_id)
        name = names[-1]
        if suffix_on_first and index == 0:
            name += _SUFFIX
        case = ET.SubElement(
            suite,
            "testcase",
            {"classname": ".".join(names[:-1]), "name": name},
        )
        if not_executed and index == 0:
            props = ET.SubElement(case, "properties")
            ET.SubElement(
                props,
                "property",
                {
                    "name": "linux_poppler",
                    "value": "LINUX_POPPLER NOT_EXECUTED: governed prefix absent",
                },
            )
    ET.ElementTree(suite).write(path, encoding="utf-8")


def _execute(source: str, directory: Path) -> None:
    previous = os.getcwd()
    os.chdir(directory)
    try:
        exec(compile(source, "ci.yml", "exec"), {"__name__": "linux_poppler_count_step"})
    finally:
        os.chdir(previous)


def _write_pair(
    directory: Path,
    no_database: tuple[str, ...],
    database: tuple[str, ...],
    *,
    no_database_tests: int | None = None,
    no_database_skipped: int = 0,
    no_database_not_executed: bool = False,
    suffix_on_first: bool = False,
) -> None:
    _write_suite(
        directory / _XML_NAME,
        no_database,
        tests=len(no_database) if no_database_tests is None else no_database_tests,
        skipped=no_database_skipped,
        not_executed=no_database_not_executed,
        suffix_on_first=suffix_on_first,
    )
    _write_suite(
        directory / _DATABASE_XML_NAME,
        database,
        tests=len(database),
        skipped=0,
        not_executed=False,
        suffix_on_first=False,
    )


def test_count_step_rejects_not_executed_skip_mismatch_and_wrong_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pass keeps each class; a property, a skip, a count mismatch, and a swap fail."""

    source = _count_step_python()
    assert _SUFFIX in source
    assert "len(actual) != len(expected)" in source
    assert "set(actual) != set(expected)" in source
    assert "set(no_database) & set(database)" in source
    assert "set(file_one) | set(file_two) != set(expected)" in source
    assert ">=" not in source
    root = str(_REPO)
    src = str(_REPO / "src")
    current = os.environ.get("PYTHONPATH", "")
    entries = [src, root]
    if current:
        entries.append(current)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(entries))
    node_ids = collected_linux_poppler_node_ids()
    no_database, database = _partition(node_ids)
    assert len(node_ids) == 113
    assert len(no_database) == 110
    assert len(database) == 3
    cases = (
        tmp_path / "pass",
        tmp_path / "not-executed",
        tmp_path / "skip",
        tmp_path / "mismatch",
        tmp_path / "wrong-file",
    )
    for case in cases:
        case.mkdir()
    _write_pair(cases[0], no_database, database, suffix_on_first=True)
    _write_pair(cases[1], no_database, database, no_database_not_executed=True)
    _write_pair(cases[2], no_database, database, no_database_skipped=1)
    _write_pair(cases[3], no_database, database, no_database_tests=len(no_database) + 1)
    swapped_no_database = (database[0], *no_database[1:])
    swapped_database = (no_database[0], *database[1:])
    _write_pair(cases[4], swapped_no_database, swapped_database)
    _execute(source, cases[0])
    for failing in cases[1:]:
        with pytest.raises(SystemExit):
            _execute(source, failing)
