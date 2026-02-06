# New PC Bootstrap Guide

Canonical setup guide for IDIS on a fresh Windows development machine.

## Prerequisites

- **Python 3.11+** (`py -3.11 --version` to confirm)
- **Docker Desktop** (running)
- **Git**

## 1. Clone & Verify

```powershell
git clone https://github.com/albarami/IDIS.git
cd IDIS
git fetch --all --tags
git tag -l | findstr legacy-v6.3-asbuilt
```

## 2. Python Environment

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip setuptools wheel
python -m pip install -e ".[dev]"
python -m pre_commit install
```

## 3. Local Configuration

```powershell
Copy-Item .env.example .env
# Edit .env if needed (never commit .env)
```

## 4. Start Infrastructure

```powershell
docker compose up -d
```

This starts Postgres (with pgvector), Redis, and Neo4j.

## 5. Verify Infrastructure

```powershell
docker compose ps                          # All services healthy
curl http://localhost:8000/health           # Returns OK (or use: irm http://localhost:8000/health)
```

## 6. Run Gates

```powershell
.\make.bat check
.\make.bat postgres_integration            # Optional: requires IDIS_REQUIRE_POSTGRES=1
```

## Known Windows Gotchas

- Use `.\.venv\Scripts\activate` not `activate` alone.
- If `python` opens Microsoft Store, use `py -3.11` or the full venv path.
- Docker Desktop must be running before `docker compose up`.
- The `make.bat` file uses `%PYTHON_BIN%` resolved from `.venv\Scripts\python.exe` -- never bare `python`.


