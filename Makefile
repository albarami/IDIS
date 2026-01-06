.PHONY: format lint typecheck test check install dev clean

format:
	ruff format .

lint:
	ruff check .

typecheck:
	mypy src

test:
	pytest -q

check: format lint typecheck test
	@echo "All checks passed."

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

clean:
	rm -rf __pycache__ .pytest_cache .ruff_cache .mypy_cache dist build *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
