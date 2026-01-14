.PHONY: format lint typecheck test forbidden-scan check install dev clean postgres-integration

format:
	ruff format .

lint:
	ruff check .

typecheck:
	mypy src

test:
	pytest -q

forbidden-scan:
	python scripts/forbidden_scan.py

postgres-integration:
	python scripts/run_postgres_integration_local.py

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
