"""The dependency lock and its post-install verifier never import IDIS."""

from __future__ import annotations

import importlib.util
import tomllib
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml
from packaging.requirements import Requirement
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_dependency_resolution.py"
_PIP_PIN_LINE = (
    '.venv/bin/python -m pip --isolated install --index-url https://pypi.org/simple "pip==26.2.1"'
)
_DEV_INSTALL_LINE = (
    ".venv/bin/python -m pip --isolated install --index-url "
    "https://pypi.org/simple -c requirements/constraints-py311.txt "
    "--build-constraint requirements/constraints-py311.txt "
    '-e ".[dev]" setuptools wheel'
)
_SHARED_INSTALL_LINES = (
    "python -m venv .venv",
    _PIP_PIN_LINE,
    _DEV_INSTALL_LINE,
    ".venv/bin/python -m pip check",
    ".venv/bin/python scripts/verify_dependency_resolution.py",
)
_GITHUB_PATH_LINE = 'echo "$PWD/.venv/bin" >> "$GITHUB_PATH"'
_INSTALL_COMPOSITION_RUN = "\n".join(("set -euo pipefail", *_SHARED_INSTALL_LINES)) + "\n"
_MIGRATION_CEILING_RUN = "set -euo pipefail\n.venv/bin/python scripts/check_migration_ceiling.py\n"
_HISTORICAL_CEILING_RUN = "python scripts/check_migration_ceiling.py"
_VENV_MARKER = "python -m venv .venv"
_COUNT_MARKERS = (
    _VENV_MARKER,
    "setuptools wheel",
    _GITHUB_PATH_LINE,
    "--build-constraint requirements/constraints-py311.txt",
    '"pip==26.2.1"',
    "python scripts/verify_dependency_resolution.py",
    _HISTORICAL_CEILING_RUN,
)
# f99595ef per-job counts, in _COUNT_MARKERS order.
_F99595EF_JOB_COUNTS: dict[str, tuple[int, ...]] = {
    "ui-check": (0, 0, 0, 0, 0, 0, 0),
    "check": (1, 1, 1, 1, 1, 1, 1),
    "postgres-integration": (1, 1, 1, 1, 1, 1, 1),
    "evaluation-harness": (1, 1, 1, 1, 1, 1, 0),
    "container-build": (1, 1, 1, 1, 1, 1, 0),
    "k8s-validate": (0, 0, 0, 0, 0, 0, 0),
    "terraform-validate": (0, 0, 0, 0, 0, 0, 0),
    "backup-restore-drill": (1, 1, 1, 1, 1, 1, 1),
    "release-gate": (1, 1, 1, 1, 1, 1, 0),
}
_CONTAINER_BUILD_COUNTS = (2, 2, 1, 2, 2, 2, 1)


def _run_steps(workflow: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    steps: list[tuple[str, dict[str, Any]]] = []
    for name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            if isinstance(step.get("run"), str):
                steps.append((name, step))
    return steps


def _job_marker_counts(job: dict[str, Any]) -> tuple[int, ...]:
    runs = [step.get("run") for step in job.get("steps", []) if isinstance(step.get("run"), str)]
    text = "\n".join(runs)
    return tuple(text.count(marker) for marker in _COUNT_MARKERS)


class DependencyResolutionTests(unittest.TestCase):
    def load_verifier(self) -> ModuleType:
        self.assertTrue(SCRIPT.is_file(), "The installed-dependency verifier is missing")
        spec = importlib.util.spec_from_file_location("dependency_verifier", SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_requires_exact_pins(self) -> None:
        verifier = self.load_verifier()
        for invalid in ("example>=1", "example", "example==1.*", "example @ https://invalid/"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                verifier.parse_pins(invalid, {"sys_platform": "win32"})

    def test_selects_only_matching_platform_pins_and_normalizes_names(self) -> None:
        verifier = self.load_verifier()
        pins = verifier.parse_pins(
            "# generated\nSome_Package==1.2; sys_platform == 'win32'\n"
            "some-package==2.0; sys_platform == 'linux'\n",
            {"sys_platform": "win32"},
        )
        self.assertEqual(pins, {"some-package": "1.2"})

    def test_rejects_conflicting_active_pins(self) -> None:
        verifier = self.load_verifier()
        with self.assertRaises(ValueError):
            verifier.parse_pins("example==1\nexample==2", {"sys_platform": "win32"})

    def test_rejects_unknown_or_executable_markers(self) -> None:
        verifier = self.load_verifier()
        for marker in ("unknown == 'x'", "__import__('os')"):
            with self.subTest(marker=marker), self.assertRaises(ValueError):
                verifier.parse_pins(f"example==1; {marker}", {"sys_platform": "win32"})

    def test_supports_boolean_marker_combinations(self) -> None:
        verifier = self.load_verifier()
        text = (
            "example==1; python_version == '3.11' and "
            "(sys_platform == 'win32' or sys_platform == 'linux')\n"
            "other==2; sys_platform not in 'darwin,linux'\n"
        )
        self.assertEqual(
            verifier.parse_pins(text, {"python_version": "3.11", "sys_platform": "win32"}),
            {"example": "1", "other": "2"},
        )

    def test_rejects_unpinned_and_mismatched_installed_distributions(self) -> None:
        verifier = self.load_verifier()
        issues = verifier.verify_installed(
            {"example": "1"}, [("example", "2", False), ("untracked", "3", False)]
        )
        self.assertEqual(issues, ["VERSION_MISMATCH: example", "UNPINNED: untracked"])

    def test_only_local_idis_is_exempt(self) -> None:
        verifier = self.load_verifier()
        self.assertEqual(verifier.verify_installed({}, [("idis", "6.3.0", True)]), [])
        self.assertEqual(
            verifier.verify_installed({}, [("idis", "6.3.0", False)]), ["UNPINNED: idis"]
        )

    def test_constraints_do_not_require_uninstalled_optional_distributions(self) -> None:
        verifier = self.load_verifier()
        self.assertEqual(verifier.verify_installed({"optional": "1"}, []), [])

    def test_version_markers_compare_numbers_not_lexicographic_strings(self) -> None:
        verifier = self.load_verifier()
        self.assertEqual(
            verifier.parse_pins(
                "old-only==1; python_full_version < '3.11.3'\n"
                "current==2; python_full_version >= '3.11.3'\n",
                {"python_full_version": "3.11.12"},
            ),
            {"current": "2"},
        )

    def test_version_equality_uses_standard_pep_508_normalization(self) -> None:
        verifier = self.load_verifier()
        self.assertEqual(
            verifier.parse_pins(
                "example==1; python_full_version == '3.11'",
                {"python_full_version": "3.11.0"},
            ),
            {"example": "1"},
        )

    def test_rejects_duplicate_installed_metadata(self) -> None:
        verifier = self.load_verifier()
        self.assertEqual(
            verifier.verify_installed({"example": "1"}, [("example", "1", False)] * 2),
            ["DUPLICATE_DISTRIBUTION: example"],
        )

    def test_rejects_invalid_distribution_names_before_reporting_them(self) -> None:
        verifier = self.load_verifier()
        with self.assertRaises(ValueError):
            verifier.verify_installed({}, [("example\nPRIVATE_MARKER", "1", False)])

    def test_local_source_requires_a_file_url_not_editable_flag_alone(self) -> None:
        verifier = self.load_verifier()
        self.assertTrue(verifier.is_local_source('{"url": "file:///workspace/idis"}'))
        for value in (
            None,
            "{}",
            '{"url": "https://example.invalid/idis", "dir_info": {"editable": true}}',
            '{"url": "file://remote-host/share/idis"}',
        ):
            with self.subTest(value=value):
                self.assertFalse(verifier.is_local_source(value))


class DependencyWiringTests(unittest.TestCase):
    def test_required_caller_bootstrap_precedes_private_overlay_without_global_db_exports(
        self,
    ) -> None:
        workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
        self._assert_required_caller_wiring(workflow)

    def _assert_required_caller_wiring(self, workflow: dict[str, Any]) -> None:
        job = workflow["jobs"]["check"]
        self.assertIn("postgres", job["services"])
        self.assertIn("@sha256:", job["services"]["postgres"]["image"])
        steps = job["steps"]
        setup = [i for i, step in enumerate(steps) if step.get("id") == "required-callers-setup"]
        self.assertEqual(len(setup), 1)
        index = setup[0]
        ceiling = next(
            i
            for i, s in enumerate(steps)
            if s.get("run") == "python scripts/check_migration_ceiling.py"
        )
        overlay = next(i for i, s in enumerate(steps) if s.get("id") == "private-overlay")
        governance_upload = next(
            i for i, s in enumerate(steps) if s.get("name") == "Upload prompt governance report"
        )
        self.assertLess(ceiling, index)
        self.assertLess(index, governance_upload)
        self.assertLess(index, overlay)
        step = steps[index]
        self.assertEqual(step["run"], "python scripts/pg_bootstrap_ci.py --required-preview check")
        self.assertNotIn("if", step)
        self.assertNotIn("continue-on-error", step)
        self.assertEqual(
            set(step["env"]),
            {
                "IDIS_PG_HOST",
                "IDIS_PG_PORT",
                "IDIS_CI_POSTGRES_SERVICE_ID",
                "PG_ADMIN_USER",
                "PG_ADMIN_PASSWORD",
                "IDIS_PG_APP_USER",
                "IDIS_PG_APP_PASSWORD",
            },
        )
        self.assertNotIn("IDIS_REQUIRE_POSTGRES", job.get("env", {}))
        self.assertFalse(any(name.startswith("IDIS_DATABASE_") for name in job.get("env", {})))
        self.assertNotIn("secrets.", str(step))
        self.assertNotIn("IDIS_CI_READ_TOKEN", str(step))

    def test_required_caller_wiring_rejects_order_scope_and_success_bypasses(self) -> None:
        for mutation in ("late", "early", "always", "ignored", "global", "no-service"):
            with self.subTest(mutation=mutation):
                workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
                job = workflow["jobs"]["check"]
                steps = job["steps"]
                setup = next(s for s in steps if s.get("id") == "required-callers-setup")
                if mutation == "late":
                    steps.remove(setup)
                    steps.append(setup)
                elif mutation == "early":
                    steps.remove(setup)
                    steps.insert(0, setup)
                elif mutation == "always":
                    setup["if"] = "always()"
                elif mutation == "ignored":
                    setup["continue-on-error"] = True
                elif mutation == "global":
                    job["env"]["IDIS_DATABASE_URL"] = "postgresql://foreign"
                else:
                    del job["services"]["postgres"]
                with self.assertRaises((AssertionError, StopIteration)):
                    self._assert_required_caller_wiring(workflow)

    def test_declared_fastapi_floor_covers_the_qualified_public_routing_api(self) -> None:
        project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        declared = [
            requirement
            for value in project["project"]["dependencies"]
            if (requirement := Requirement(value)).name == "fastapi"
        ]
        self.assertEqual(len(declared), 1)
        self.assertEqual(str(declared[0].specifier), ">=0.143.3")
        for unsupported in ("0.109.0", "0.137.2", "0.143.2"):
            with self.subTest(unsupported=unsupported):
                self.assertNotIn(Version(unsupported), declared[0].specifier)
        constraints = (REPO_ROOT / "requirements/constraints-py311.txt").read_text(encoding="utf-8")
        pinned = [
            Requirement(line) for line in constraints.splitlines() if line.startswith("fastapi==")
        ]
        self.assertEqual(len(pinned), 1)
        self.assertEqual(str(pinned[0].specifier), "==0.143.3")
        self.assertIn(Version("0.143.3"), declared[0].specifier)

    def test_resolution_target_and_pip_configuration_are_explicit(self) -> None:
        project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(
            project["tool"]["uv"]["environments"],
            ["python_version == '3.11' and (sys_platform == 'win32' or sys_platform == 'linux')"],
        )
        workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("PIP_CONFIG_FILE: /dev/null", workflow)
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("PIP_CONFIG_FILE=/dev/null", dockerfile)

    def test_every_ci_install_uses_a_fresh_environment_and_pinned_seed_tools(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count("python -m venv .venv"), 7)
        self.assertEqual(workflow.count("setuptools wheel"), 7)
        self.assertEqual(workflow.count('echo "$PWD/.venv/bin" >> "$GITHUB_PATH"'), 6)

    def test_ci_retains_checks_and_bounds_all_dependency_installations(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count("--build-constraint requirements/constraints-py311.txt"), 7)
        self.assertEqual(workflow.count('"pip==26.2.1"'), 7)
        self.assertEqual(workflow.count("python scripts/verify_dependency_resolution.py"), 7)
        self.assertIn("pytest tests/test_slice99_backup_restore_postgres.py -v", workflow)
        self.assertEqual(workflow.count("python scripts/check_migration_ceiling.py"), 4)
        self._assert_required_ci_job_execution(yaml.safe_load(workflow))

    def _assert_required_ci_job_execution(self, workflow: dict[str, Any]) -> None:
        jobs = workflow["jobs"]
        self.assertIn(
            "make check",
            [step.get("run") for step in jobs["check"]["steps"]],
            "check must retain the exact make check step",
        )
        for job, command in (
            ("check", "make check"),
            ("postgres-integration", "python scripts/pg_bootstrap_ci.py --export-github-env"),
            ("backup-restore-drill", "pytest tests/test_slice99_backup_restore_postgres.py -v"),
        ):
            steps = jobs[job]["steps"]
            ceiling = [
                index
                for index, step in enumerate(steps)
                if step.get("run") == "python scripts/check_migration_ceiling.py"
            ]
            consumer = [index for index, step in enumerate(steps) if command in step.get("run", "")]
            self.assertEqual(len(ceiling), 1, f"{job} must retain one migration ceiling step")
            self.assertEqual(len(consumer), 1, f"{job} must retain one required command")
            self.assertLess(ceiling[0], consumer[0], f"{job} must check the ceiling first")

    def test_ci_job_execution_guard_rejects_missing_make_check(self) -> None:
        workflow = yaml.safe_load(
            (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        )
        steps = workflow["jobs"]["check"]["steps"]
        steps[:] = [step for step in steps if step.get("run") != "make check"]

        with self.assertRaisesRegex(AssertionError, "check must retain the exact make check step"):
            self._assert_required_ci_job_execution(workflow)

    def test_ci_job_execution_guard_rejects_reordered_migration_ceilings(self) -> None:
        for job, command in (
            ("check", "make check"),
            ("postgres-integration", "python scripts/pg_bootstrap_ci.py --export-github-env"),
            ("backup-restore-drill", "pytest tests/test_slice99_backup_restore_postgres.py -v"),
        ):
            with self.subTest(job=job):
                workflow = yaml.safe_load(
                    (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
                )
                steps = workflow["jobs"][job]["steps"]
                ceiling = next(
                    index
                    for index, step in enumerate(steps)
                    if step.get("run") == "python scripts/check_migration_ceiling.py"
                )
                consumer = next(
                    index for index, step in enumerate(steps) if command in step.get("run", "")
                )
                steps[ceiling], steps[consumer] = steps[consumer], steps[ceiling]

                with self.assertRaisesRegex(AssertionError, f"{job} must check the ceiling first"):
                    self._assert_required_ci_job_execution(workflow)

    def test_container_build_pins_actual_install_and_ceiling_literals(self) -> None:
        workflow_text = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self._assert_container_build_dependency_pins(workflow_text)

    def _assert_container_build_dependency_pins(self, workflow_text: str) -> None:
        workflow: dict[str, Any] = yaml.safe_load(workflow_text)
        self._assert_install_composition_pin(workflow)
        self._assert_migration_ceiling_pin(workflow)
        self._assert_f99595ef_job_counts_except_container_build(workflow, workflow_text)

    def _assert_install_composition_pin(self, workflow: dict[str, Any]) -> None:
        located = [
            (name, step)
            for name, step in _run_steps(workflow)
            if step.get("id") == "install-composition"
        ]
        self.assertEqual(
            [name for name, _step in located],
            ["container-build"],
            "install-composition must be the container-build install block",
        )
        composition = located[0][1]
        self.assertEqual(composition["run"], _INSTALL_COMPOSITION_RUN)
        check_install = next(
            step
            for step in workflow["jobs"]["check"]["steps"]
            if step.get("name") == "Install dependencies"
        )
        shared_count = len(_SHARED_INSTALL_LINES)
        composition_lines = composition["run"].splitlines()
        check_lines = check_install["run"].splitlines()
        self.assertEqual(composition_lines[1:], check_lines[:shared_count])
        self.assertEqual(composition_lines[1:], list(_SHARED_INSTALL_LINES))
        self.assertEqual(check_lines[shared_count:], [_GITHUB_PATH_LINE])
        self.assertNotIn(_GITHUB_PATH_LINE, composition["run"])
        installs = [
            (name, step) for name, step in _run_steps(workflow) if _VENV_MARKER in step["run"]
        ]
        self.assertEqual(len(installs), 7)
        self.assertEqual(
            [step["run"] for _name, step in installs if step.get("id") == "install-composition"],
            [_INSTALL_COMPOSITION_RUN],
        )

    def _assert_migration_ceiling_pin(self, workflow: dict[str, Any]) -> None:
        located = [
            (name, step)
            for name, step in _run_steps(workflow)
            if step.get("id") == "migration-ceiling"
        ]
        self.assertEqual(
            [name for name, _step in located],
            ["container-build"],
            "migration-ceiling must be the container-build ceiling command",
        )
        self.assertEqual(located[0][1]["run"], _MIGRATION_CEILING_RUN)
        ceilings = [
            (name, step)
            for name, step in _run_steps(workflow)
            if _HISTORICAL_CEILING_RUN in step["run"]
        ]
        self.assertEqual(len(ceilings), 4)
        historical = [step for _name, step in ceilings if step.get("id") != "migration-ceiling"]
        self.assertEqual(
            [step.get("run") for step in historical],
            [_HISTORICAL_CEILING_RUN] * 3,
        )

    def _assert_f99595ef_job_counts_except_container_build(
        self, workflow: dict[str, Any], workflow_text: str
    ) -> None:
        self.assertEqual(set(workflow["jobs"]), set(_F99595EF_JOB_COUNTS))
        for name, expected in _F99595EF_JOB_COUNTS.items():
            if name == "container-build":
                continue
            self.assertEqual(_job_marker_counts(workflow["jobs"][name]), expected, name)
        self.assertEqual(
            _job_marker_counts(workflow["jobs"]["container-build"]),
            _CONTAINER_BUILD_COUNTS,
            "container-build",
        )
        totals = [0] * len(_COUNT_MARKERS)
        for job in workflow["jobs"].values():
            for index, count in enumerate(_job_marker_counts(job)):
                totals[index] += count
        for marker, total in zip(_COUNT_MARKERS, totals, strict=True):
            self.assertEqual(workflow_text.count(marker), total, marker)

    def test_docker_uses_the_same_runtime_and_build_constraints(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY requirements/ ./requirements/", dockerfile)
        self.assertIn("--build-constraint requirements/constraints-py311.txt", dockerfile)
        self.assertIn("-c requirements/constraints-py311.txt", dockerfile)
        self.assertIn('"pip==26.2.1"', dockerfile)
        self.assertIn('"idis.runtime.strict_launcher"', dockerfile)


if __name__ == "__main__":
    unittest.main()
