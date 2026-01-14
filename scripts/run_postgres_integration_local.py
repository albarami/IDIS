#!/usr/bin/env python3
"""Local Postgres integration test runner.

This script provides a one-command solution to run the Postgres integration
tests locally with a Docker-based PostgreSQL instance.

Usage:
    python scripts/run_postgres_integration_local.py

Requirements:
    - Docker must be installed and running
    - Python 3.10+ with psycopg2-binary installed

The script will:
    1. Start a PostgreSQL container (postgres:16)
    2. Wait for the database to be ready
    3. Run the CI bootstrap to create app role and run migrations
    4. Execute the Postgres integration test suite
    5. Tear down the container (even on failure)
    6. Exit with pytest's exit code
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager

CONTAINER_NAME = "idis-postgres-integration-test"
POSTGRES_IMAGE = "postgres:16"
POSTGRES_PORT = 15432
POSTGRES_USER = "postgres"
POSTGRES_PASSWORD = "postgres"
POSTGRES_DB = "postgres"
APP_USER = "idis_app"
APP_PASSWORD = "idis_app_pw"
TEST_DB = "idis_test"


def log(msg: str) -> None:
    """Print a log message with prefix."""
    print(f"[postgres-local] {msg}", flush=True)


def run_cmd(
    cmd: list[str], check: bool = True, capture: bool = False
) -> subprocess.CompletedProcess:
    """Run a command and optionally check for errors."""
    log(f"Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
    )
    if check and result.returncode != 0:
        if capture:
            log(f"STDOUT: {result.stdout}")
            log(f"STDERR: {result.stderr}")
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}")
    return result


def is_docker_available() -> bool:
    """Check if Docker is available."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Check if a TCP port is open."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (TimeoutError, ConnectionRefusedError, OSError):
        return False


def container_exists(name: str) -> bool:
    """Check if a Docker container exists (running or stopped)."""
    result = subprocess.run(
        ["docker", "container", "inspect", name],
        capture_output=True,
    )
    return result.returncode == 0


def stop_and_remove_container(name: str) -> None:
    """Stop and remove a Docker container if it exists."""
    if container_exists(name):
        log(f"Stopping and removing existing container: {name}")
        subprocess.run(["docker", "stop", name], capture_output=True)
        subprocess.run(["docker", "rm", name], capture_output=True)


def start_postgres_container() -> None:
    """Start the PostgreSQL Docker container."""
    log(f"Starting PostgreSQL container on port {POSTGRES_PORT}...")
    run_cmd(
        [
            "docker",
            "run",
            "--name",
            CONTAINER_NAME,
            "-d",
            "-p",
            f"{POSTGRES_PORT}:5432",
            "-e",
            f"POSTGRES_USER={POSTGRES_USER}",
            "-e",
            f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
            "-e",
            f"POSTGRES_DB={POSTGRES_DB}",
            POSTGRES_IMAGE,
        ]
    )


def wait_for_postgres(host: str, port: int, max_retries: int = 30, delay: float = 1.0) -> None:
    """Wait for PostgreSQL to be ready to accept connections."""
    log(f"Waiting for PostgreSQL at {host}:{port}...")
    for attempt in range(max_retries):
        if is_port_open(host, port):
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    CONTAINER_NAME,
                    "pg_isready",
                    "-U",
                    POSTGRES_USER,
                ],
                capture_output=True,
            )
            if result.returncode == 0:
                log("PostgreSQL is ready")
                return
        if attempt < max_retries - 1:
            time.sleep(delay)
    raise RuntimeError(f"PostgreSQL not ready after {max_retries} attempts")


def run_bootstrap() -> None:
    """Run the CI bootstrap script to set up the database."""
    log("Running CI bootstrap...")

    env = os.environ.copy()
    env["IDIS_PG_HOST"] = "127.0.0.1"
    env["IDIS_PG_PORT"] = str(POSTGRES_PORT)
    env["IDIS_PG_DB_NAME"] = TEST_DB
    env["PG_ADMIN_USER"] = POSTGRES_USER
    env["PG_ADMIN_PASSWORD"] = POSTGRES_PASSWORD
    env["IDIS_PG_APP_USER"] = APP_USER
    env["IDIS_PG_APP_PASSWORD"] = APP_PASSWORD

    result = subprocess.run(
        [sys.executable, "scripts/pg_bootstrap_ci.py"],
        env=env,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Bootstrap failed with exit code {result.returncode}")
    log("Bootstrap completed successfully")


def run_integration_tests() -> int:
    """Run the Postgres integration tests and return the exit code."""
    log("Running Postgres integration tests...")

    admin_url = (
        f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@127.0.0.1:{POSTGRES_PORT}/{TEST_DB}"
    )
    app_url = f"postgresql://{APP_USER}:{APP_PASSWORD}@127.0.0.1:{POSTGRES_PORT}/{TEST_DB}"

    env = os.environ.copy()
    env["IDIS_DATABASE_ADMIN_URL"] = admin_url
    env["IDIS_DATABASE_URL"] = app_url
    env["IDIS_REQUIRE_POSTGRES"] = "1"

    test_files = [
        "tests/test_api_deals_postgres.py",
        "tests/test_api_claims_postgres.py",
        "tests/test_postgres_rls_and_audit_immutability.py",
    ]

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-v"] + test_files,
        env=env,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    return result.returncode


@contextmanager
def postgres_container() -> Generator[None, None, None]:
    """Context manager for PostgreSQL container lifecycle."""
    stop_and_remove_container(CONTAINER_NAME)
    try:
        start_postgres_container()
        wait_for_postgres("127.0.0.1", POSTGRES_PORT)
        yield
    finally:
        log("Tearing down PostgreSQL container...")
        stop_and_remove_container(CONTAINER_NAME)
        log("Cleanup complete")


def main() -> int:
    """Main entry point."""
    log("=" * 60)
    log("IDIS Local Postgres Integration Test Runner")
    log("=" * 60)

    if not is_docker_available():
        log("ERROR: Docker is not available. Please install and start Docker.")
        return 1

    try:
        with postgres_container():
            run_bootstrap()
            exit_code = run_integration_tests()

            if exit_code == 0:
                log("=" * 60)
                log("All Postgres integration tests PASSED")
                log("=" * 60)
            else:
                log("=" * 60)
                log(f"Postgres integration tests FAILED (exit code: {exit_code})")
                log("=" * 60)

            return exit_code
    except Exception as e:
        log(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
