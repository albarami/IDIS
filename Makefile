.PHONY: format lint typecheck test forbidden-scan check install dev clean postgres-integration ui-check

format:
	ruff format .

lint:
	ruff check .

typecheck:
	mypy src/idis --ignore-missing-imports

test:
	pytest -q

forbidden-scan:
	python scripts/forbidden_scan.py

postgres-integration:
	python scripts/run_postgres_integration_local.py

ui-check:
	cd ui && npm ci && npm run lint && npm run typecheck && npm run test && npm run build

check: format lint typecheck test forbidden-scan
	@echo "All checks passed."

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

clean:
	rm -rf __pycache__ .pytest_cache .ruff_cache .mypy_cache dist build *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
