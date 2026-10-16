"""The dependency lock and its post-install verifier never import IDIS."""

from __future__ import annotations

import importlib.util
import tomllib
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_dependency_resolution.py"


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
        self.assertEqual(workflow.count("python -m venv .venv"), 6)
        self.assertEqual(workflow.count("setuptools wheel"), 6)
        self.assertEqual(workflow.count('echo "$PWD/.venv/bin" >> "$GITHUB_PATH"'), 6)

    def test_ci_retains_checks_and_bounds_all_dependency_installations(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count("--build-constraint requirements/constraints-py311.txt"), 6)
        self.assertEqual(workflow.count('"pip==26.2.1"'), 6)
        self.assertEqual(workflow.count("python scripts/verify_dependency_resolution.py"), 6)
        self.assertIn("pytest tests/test_slice99_backup_restore_postgres.py -v", workflow)
        self.assertEqual(workflow.count("python scripts/check_migration_ceiling.py"), 3)
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

    def test_docker_uses_the_same_runtime_and_build_constraints(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY requirements/ ./requirements/", dockerfile)
        self.assertIn("--build-constraint requirements/constraints-py311.txt", dockerfile)
        self.assertIn("-c requirements/constraints-py311.txt", dockerfile)
        self.assertIn('"pip==26.2.1"', dockerfile)
        self.assertIn('"idis.runtime.strict_launcher"', dockerfile)


if __name__ == "__main__":
    unittest.main()
