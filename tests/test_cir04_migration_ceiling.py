"""Offline, stdlib-only regressions for the authorized 0038 migration ceiling."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_migration_ceiling.py"


class MigrationCeilingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.versions = Path(self.temporary.name)
        for number in range(1, 39):
            self.write_revision(number)

    def write_revision(self, number: int, *, body: str = "") -> Path:
        revision = f"{number:04d}"
        parent = repr(f"{number - 1:04d}") if number > 1 else "None"
        path = self.versions / f"{revision}_fixture.py"
        path.write_text(
            f"revision = {revision!r}\ndown_revision = {parent}\n"
            f"branch_labels = None\ndepends_on = None\n{body}",
            encoding="utf-8",
        )
        return path

    def run_guard(self, versions: Path | None = None) -> subprocess.CompletedProcess[str]:
        self.assertTrue(SCRIPT.is_file(), "The fail-closed migration ceiling guard is missing")
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--versions-dir", str(versions or self.versions)],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def assert_rejected(self) -> None:
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MIGRATION_CEILING_REJECTED", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_accepts_exact_literal_chain(self) -> None:
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("38 revisions; single head 0038", result.stdout)

    def test_accepts_current_repository_without_importing_migrations(self) -> None:
        result = self.run_guard(REPO_ROOT / "src/idis/persistence/migrations/versions")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_does_not_execute_migration_bodies(self) -> None:
        marker = self.versions / "executed"
        self.write_revision(38, body=f"raise RuntimeError({str(marker)!r})\n")
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())

    def test_rejects_0039(self) -> None:
        self.write_revision(39)
        self.assert_rejected()

    def test_rejects_missing_revision(self) -> None:
        (self.versions / "0017_fixture.py").unlink()
        self.assert_rejected()

    def test_rejects_duplicate_revision(self) -> None:
        source = self.versions / "0038_fixture.py"
        (self.versions / "0038_duplicate.py").write_bytes(source.read_bytes())
        self.assert_rejected()

    def test_rejects_alternate_root_head_cycle_or_parent(self) -> None:
        for parent in ("None", "'0036'", "'0038'", "'9999'", "('0036', '0037')"):
            with self.subTest(parent=parent):
                path = self.write_revision(38)
                path.write_text(
                    path.read_text(encoding="utf-8").replace("'0037'", parent),
                    encoding="utf-8",
                )
                self.assert_rejected()

    def test_rejects_computed_metadata(self) -> None:
        for replacement in ("str(38)", "'00' + '38'", "f'{38:04d}'"):
            with self.subTest(replacement=replacement):
                path = self.write_revision(38)
                path.write_text(
                    path.read_text(encoding="utf-8").replace("'0038'", replacement),
                    encoding="utf-8",
                )
                self.assert_rejected()

    def test_rejects_metadata_reassignment_including_conditional(self) -> None:
        for body in ("revision = '0039'\n", "if True:\n    revision = '0039'\n"):
            with self.subTest(body=body):
                self.write_revision(38, body=body)
                self.assert_rejected()

    def test_rejects_import_definition_and_dynamic_metadata_rebinding(self) -> None:
        for body in (
            "from unknown import revision\n",
            "import unknown as revision\n",
            "def revision():\n    return '0039'\n",
            "globals()['revision'] = '0039'\n",
            "globals().update(revision='0039')\n",
            "exec(\"revision = '0039'\")\n",
        ):
            with self.subTest(body=body):
                self.write_revision(38, body=body)
                self.assert_rejected()

    def test_rejects_branch_labels_or_dependencies(self) -> None:
        for field in ("branch_labels", "depends_on"):
            with self.subTest(field=field):
                path = self.write_revision(38)
                path.write_text(
                    path.read_text(encoding="utf-8").replace(
                        f"{field} = None", f"{field} = ('other',)"
                    ),
                    encoding="utf-8",
                )
                self.assert_rejected()

    def test_rejects_filename_mismatch(self) -> None:
        (self.versions / "0038_fixture.py").rename(self.versions / "0039_wrong.py")
        self.assert_rejected()

    def test_rejects_malformed_or_missing_metadata_without_echoing_source(self) -> None:
        for content in ("PRIVATE_SOURCE_MARKER = (", "PRIVATE_SOURCE_MARKER = 1\n"):
            with self.subTest(content=content):
                (self.versions / "0038_fixture.py").write_text(content, encoding="utf-8")
                result = self.run_guard()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("PRIVATE_SOURCE_MARKER", result.stderr)
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
