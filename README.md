# IDIS - Institutional Deal Intelligence System (VC Edition)

**Version:** 6.3

IDIS is an enterprise-grade AI Investment Analyst Layer for Venture Capital. It ingests deal materials, enriches them with external intelligence, performs deterministic financial calculations, runs multi-agent analysis and debate, and produces IC-ready outputs with evidence governance and auditability.

## Core Trust Invariants

- **No-Free-Facts**: Every factual statement must reference `claim_id` or `calc_id`
- **Deterministic Numerics**: All numbers from Python calc engines, never LLMs
- **Sanad Trust Framework**: Every claim has an evidence chain with A/B/C/D grading
- **Muḥāsabah Gate**: Every agent output requires self-audit record
- **Audit Completeness**: Every mutation emits an immutable audit event
- **Tenant Isolation**: All data is tenant-scoped with strict isolation

## Quick Start

### Prerequisites

- Python 3.11+
- Make

### Installation

```bash
# Install in development mode with all dev dependencies
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install
```

### Running

**Enterprise Mode (Postgres):**

```bash
# Start Postgres container (if not already running)
docker run -d --name idis-postgres \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=idis_test \
  -p 15433:5432 \
  postgres:16

# Wait for Postgres to be ready
sleep 5

# Run migrations
export IDIS_DATABASE_ADMIN_URL="postgresql://postgres:postgres@127.0.0.1:15433/idis_test"
alembic -c src/idis/persistence/migrations/alembic.ini upgrade head

# Start backend with Postgres
export IDIS_DATABASE_URL="postgresql://idis_app:idis_app_pw@127.0.0.1:15433/idis_test"
export IDIS_DATABASE_ADMIN_URL="postgresql://postgres:postgres@127.0.0.1:15433/idis_test"
export IDIS_API_KEYS_JSON='{"test-key-123":{"tenant_id":"00000000-0000-0000-0000-000000000001","actor_id":"00000000-0000-0000-0000-000000000100","name":"Local Admin","timezone":"UTC","data_region":"us-east-1","roles":["ADMIN","ANALYST"]}}'
uvicorn idis.app:app --reload

# Health check
curl http://localhost:8000/health
```

**Development Mode (In-Memory Fallback):**

```bash
# Run without Postgres (in-memory repositories only)
uvicorn idis.app:app --reload

# Health check
curl http://localhost:8000/health
```

**Note:** The in-memory fallback is for testing only. Production deployments must use Postgres.

### Development Commands

**With GNU Make (Linux/macOS):**
```bash
make format     # Format code with ruff
make lint       # Lint code with ruff
make typecheck  # Type check with mypy
make test       # Run tests with pytest
make check      # Run all checks (format, lint, typecheck, test)
```

**With py-make (Windows or without GNU Make):**
```bash
# Install py-make (included in dev dependencies)
pip install py-make

# Run targets using pymake
pymake format
pymake lint
pymake typecheck
pymake test
pymake check
```

**With make.bat (Windows native):**
```cmd
make format
make lint
make typecheck
make test
make check
```

**Direct Commands (any platform, no make required):**
```bash
ruff format .                              # Format
ruff check .                               # Lint
python -m mypy src/idis --ignore-missing-imports  # Typecheck
python -m pytest                           # Test
python scripts/forbidden_scan.py           # Forbidden pattern scan
```

All gate commands must pass before pushing to `main`.

## Project Structure

```
IDIS/
├── docs/           # Documentation (v6.3 specs)
├── openapi/        # OpenAPI specification
├── schemas/        # JSON schemas
├── src/idis/       # Application source code
├── tests/          # Test suite
├── scripts/        # Utility scripts
└── .github/        # GitHub Actions workflows
```

## Documentation

See `docs/00_README_IDIS_Docs_v6_3.md` for the documentation index.

## License

Proprietary - All rights reserved.
