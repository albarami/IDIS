# CIR-04: bounded Python dependency resolution and migration admission

Status: implementation checkpoint; independent review and integrated CI evidence remain required.
Authorization: `AUTH-IMPL-20261016`, CIR-01–04. This does not authorize activation,
migration 0039, provider calls, deployment, or changes to frozen migrations.

## Resolution contract

`requirements/constraints-py311.txt` contains exact direct/transitive versions for
the runtime, development extra, and `requirements/toolchain.in`. The qualified
target is CPython 3.11 on Windows and Linux. `tool.uv.environments` deliberately
limits resolution to Python 3.11 and those two operating systems; supplying
`--python-version 3.11` alone would admit later Python versions in universal mode.

The installer remains pip and the project backend remains Hatchling. uv 0.13.1
is a development lock compiler only; it does not replace IDIS runtime execution.
Resolution uses public PyPI, a fixed upload cutoff `2026-10-16T16:20:36Z`, and no
dependency overrides. An existing upstream `>=3.6,` metadata warning was normalized
by uv during initial resolution; no project requirement was overridden.

Important admitted versions:

| Dependency | Version | Reason |
| --- | --- | --- |
| Anthropic SDK | 0.125.0 | CIR-01 qualified the existing temperature-bearing legacy and frozen production requests. SDK 1.x rejects that argument. This explicit compatibility exception preserves the request contract; it is not a checker downgrade. |
| Pydantic | 2.12.5 | Existing frozen-schema constraint retained. |
| FastAPI / Starlette | 0.143.3 / 1.8.0 | Current family retained; CIR-02 handles its HTTPX2 response/error boundary. |
| HTTPX / HTTPX2 | 0.28.1 / 2.13.1 | Both families are explicit; HTTPX2 is a direct requirement because the compatibility adapter imports it. |
| SQLAlchemy / psycopg2-binary | 2.1.4 / 2.9.13 | Current versions retained; CIR-03 selects the intended explicit driver. |
| mypy / Ruff | 2.4.0 / 0.17.1 | Current checks retained, not downgraded to hide errors. |
| faster-whisper / pypdf / cryptography | 1.2.1 / 6.12.2 / 46.0.7 | Existing exact or upper-bound constraints retained. No speech model is downloaded. |
| pip / uv / Hatchling / editables | 26.2.1 / 0.13.1 / 1.32.4 / 0.6 | Installer, compiler and editable build toolchain bounded. |
| setuptools / wheel / packaging | 84.0.0 / 0.48.0 / 26.3 | Seed/build tools and standard requirement-marker parsing bounded. |

## Fresh developer environment

Use a fresh authorized checkout with any separately authorized private overlay
restored. Do not modify the preserved source worktree. The commands below do not
activate an IDIS runtime or authorize access to configured databases/providers.
Run tests only in an environment stripped of live IDIS/provider/database settings.

Create recovery checkouts with persistent configuration, for example
`git clone --config core.autocrlf=false --branch <authorized-branch> <repository> <new-directory>`.
The `--config` option belongs to `clone`; a one-command `git -c` override does not
persist for later checkouts. The constraints and before-edit golden fixture have
path-specific LF attributes, so their reviewed raw hashes also survive a later
checkout on a host whose global `core.autocrlf` is true.

Windows PowerShell (choose a new virtual-environment path if `.venv` already exists):

```powershell
py -3.11 -m venv .venv
$env:PIP_CONFIG_FILE = 'nul'
.\.venv\Scripts\python.exe -m pip --isolated install --index-url https://pypi.org/simple "pip==26.2.1"
.\.venv\Scripts\python.exe -m pip --isolated install --index-url https://pypi.org/simple -c requirements/constraints-py311.txt --build-constraint requirements/constraints-py311.txt -e ".[dev]" setuptools wheel
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts/verify_dependency_resolution.py
.\.venv\Scripts\python.exe scripts/check_migration_ceiling.py
```

Linux equivalents use `python3.11 -m venv .venv`, `PIP_CONFIG_FILE=/dev/null`, and
`.venv/bin/python`. All four full Python CI installs, both release-manifest
PyYAML installs, and the Docker builder use the same constraints. CI uses fresh
virtual environments and checks every installed distribution. Seed setuptools
and wheel are installed under the lock rather than silently accepting whatever
versions the runner's virtual environment supplied.

Both `-c` and `--build-constraint` are necessary: ordinary constraints do not bound
pip's isolated build environments. The latter also constrains Hatchling and its
editable-build dependency. The post-install verifier uses the locked `packaging`
parser and rejects unpinned, mismatched, or duplicate installed distributions;
only an IDIS distribution installed from a local file URL is exempt from the
third-party pins. It is not a replacement for `pip check` or application tests.

## Deliberate lock regeneration

Regeneration is reviewed work, not a normal install step. Use uv 0.13.1 installed
under the existing constraints. Before compiling, clear `UV_*` environment
overrides, check that no local `uv.toml` or user/system uv configuration is active,
and run from the repository root. On Windows the user/system locations are
`%APPDATA%\uv\uv.toml` and `%PROGRAMDATA%\uv\uv.toml`; on Linux inspect the
documented XDG/user/system locations. Do not print credential-bearing settings.
If such configuration exists, use a clean authorized build environment; do not
delete or alter the operator's configuration.

```text
uv pip compile pyproject.toml requirements/toolchain.in --extra dev --universal --python-version 3.11 --default-index https://pypi.org/simple --exclude-newer 2026-10-16T16:20:36Z --no-sources --no-annotate --no-header --upgrade --output-file requirements/constraints-py311.txt
```

Do not add `--config-file`: uv 0.13.1 then bypasses the project's environment
restriction, and its standalone configuration does not admit `environments`.
An explicit-config experiment was rejected after it widened the lock to later
Python/platform branches. Restore the reviewed project-discovery command above.
Review the complete generated diff, repeat the forced resolution, compare bytes,
then install and qualify both supported operating-system targets.

This is reproducible version resolution, not a claim of byte-identical wheels,
permanent package-index availability, or fully reproducible OS packages. Python
patch level, platform, lock SHA, exact source revision and test results belong in
each execution receipt. Existing Docker base digests remain pinned; apt packages
and external service images are outside this Python-resolution change.

## Migration ceiling

`scripts/check_migration_ceiling.py` is stdlib-only. It parses source with AST;
it never imports IDIS, Alembic, migration modules, or a database driver. It
requires precisely one literal revision per file and the complete linear
`0001` → `0038` chain, matching filenames, one root/head, and no branch labels or
dependencies. Missing/duplicate revisions, 0039, alternate parents, computed or
rebound metadata fail closed. The three migration-capable CI entrypoints run it
before general checks, PostgreSQL bootstrap, or the recovery drill.

This source-graph check does not prove arbitrary migration code benign, inspect
an existing database, approve migration SQL, or extend the authorized ceiling.
The existing checks/tests remain in place. The strict container-launch profile
requirement is unchanged; this packet does not bypass it to obtain a green job.

## Checkpoint evidence, 2026-10-16

- Lock SHA256: `868639106abd2111b94b31b1a3b84fd1d201a004e6d47c234140646c68197ba1`.
- Forced re-resolution with the same cutoff reproduced those exact bytes.
- Fresh Windows CPython 3.11.9 installation: `pip check` passed; 138 installed
  distributions passed the active-pin verifier. No IDIS service or provider was called.
- The batch coordinator independently reported a fresh Python 3.11-slim Linux
  installation using this same lock: `pip check` passed and 138 installed
  distributions matched active pins. The exact image/patch-level receipt remains
  with the coordinator's integrated execution evidence.
- `pytest tests/test_cir04_migration_ceiling.py tests/test_cir04_dependency_resolution.py --disable-socket -q`:
  30 passed; two existing `jsonschema.RefResolver` deprecation warnings.
- New guard and dependency tests were observed RED before implementation. A
  separate real verifier RED detected the initial unpinned setuptools seed.
  Standard PEP 508 version normalization was RED against the first custom marker
  evaluator; replacing it with the existing `packaging` parser made it GREEN.
- Focused Ruff checks and mypy over the two new scripts passed. The repository's
  existing removed-rule/unused-section notices were not suppressed.
- Integrated application compatibility, independent review, full CI and remaining
  failures are recorded by the batch coordinator. These
  focused results alone do not establish CIR batch or GL-P01 acceptance.

References: [uv universal compilation and constraints](https://docs.astral.sh/uv/pip/compile/),
[uv environment restrictions](https://docs.astral.sh/uv/reference/settings/#environments),
[uv configuration discovery](https://docs.astral.sh/uv/concepts/configuration-files/),
[pip build constraints](https://pip.pypa.io/en/stable/user_guide/#build-constraints).
