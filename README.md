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

```bash
# Run the development server
uvicorn idis.app:app --reload

# Health check
curl http://localhost:8000/health
```

### Development Commands

```bash
make format     # Format code with ruff
make lint       # Lint code with ruff
make typecheck  # Type check with mypy
make test       # Run tests with pytest
make check      # Run all checks (format, lint, typecheck, test)
```

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
