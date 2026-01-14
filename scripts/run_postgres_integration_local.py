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

Timeouts:
    - Docker health check: 10 seconds
    - Container start: 30 seconds
    - PostgreSQL readiness: 60 seconds (30 retries x 2s)
    - Bootstrap: 120 seconds
    - Test execution: 600 seconds (10 minutes)

Exit codes:
    0 - All tests passed
    1 - Test failures or runtime errors
    2 - Docker not available or unhealthy
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

CONTAINER_NAME = "idis-postgres-integration-test"
POSTGRES_IMAGE = "postgres:16"
POSTGRES_PORT = 15432
POSTGRES_USER = "postgres"
POSTGRES_PASSWORD = "postgres"
POSTGRES_DB = "postgres"
APP_USER = "idis_app"
APP_PASSWORD = "idis_app_pw"
TEST_DB = "idis_test"

TIMEOUT_DOCKER_INFO = 10
TIMEOUT_CONTAINER_START = 30
TIMEOUT_CONTAINER_STOP = 15
TIMEOUT_PG_READY_TOTAL = 60
TIMEOUT_BOOTSTRAP = 120
TIMEOUT_TESTS = 600

ARTIFACTS_DIR = Path("artifacts/postgres_integration")


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


def redact_credentials(text: str) -> str:
    """Redact credentials from URLs in log output."""
    return re.sub(r"://[^:]+:[^@]+@", "://***:***@", text)


def ensure_artifacts_dir() -> None:
    """Create artifacts directory if it doesn't exist."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


def write_artifact(name: str, content: str) -> None:
    """Write content to an artifact file with credentials redacted."""
    ensure_artifacts_dir()
    artifact_path = ARTIFACTS_DIR / name
    artifact_path.write_text(redact_credentials(content), encoding="utf-8")
    log(f"Artifact written: {artifact_path}")


def check_docker_health() -> bool:
    """Check if Docker daemon is available and responding within timeout.

    Returns True if Docker is healthy, False otherwise.
    Exits with code 2 if Docker times out (indicates hang condition).
    """
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_DOCKER_INFO,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        log(
            f"ERROR: Docker not responding within {TIMEOUT_DOCKER_INFO}s. "
            "Start Docker Desktop or ensure dockerd is running."
        )
        sys.exit(2)
    except FileNotFoundError:
        log("ERROR: Docker executable not found. Please install Docker.")
        sys.exit(2)


def is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Check if a TCP port is open."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (TimeoutError, ConnectionRefusedError, OSError):
        return False


def container_exists(name: str) -> bool:
    """Check if a Docker container exists (running or stopped)."""
    try:
        result = subprocess.run(
            ["docker", "container", "inspect", name],
            capture_output=True,
            timeout=TIMEOUT_DOCKER_INFO,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def stop_and_remove_container(name: str) -> None:
    """Stop and remove a Docker container if it exists."""
    if container_exists(name):
        log(f"Stopping and removing existing container: {name}")
        try:
            subprocess.run(
                ["docker", "stop", name],
                capture_output=True,
                timeout=TIMEOUT_CONTAINER_STOP,
            )
        except subprocess.TimeoutExpired:
            log(f"Warning: docker stop timed out for {name}, forcing removal")
        try:
            subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True,
                timeout=TIMEOUT_CONTAINER_STOP,
            )
        except subprocess.TimeoutExpired:
            log(f"Warning: docker rm timed out for {name}")


def start_postgres_container() -> None:
    """Start the PostgreSQL Docker container with timeout."""
    log(f"Starting PostgreSQL container on port {POSTGRES_PORT}...")
    try:
        result = subprocess.run(
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
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_CONTAINER_START,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start container: {result.stderr}")
        log(f"Container started: {result.stdout.strip()[:12]}")
    except subprocess.TimeoutExpired as err:
        raise RuntimeError(
            f"Container start timed out after {TIMEOUT_CONTAINER_START}s"
        ) from err


def wait_for_postgres(host: str, port: int, max_retries: int = 30, delay: float = 2.0) -> None:
    """Wait for PostgreSQL to be ready to accept connections with timeout."""
    log(f"Waiting for PostgreSQL at {host}:{port} (max {max_retries * delay}s)...")
    for attempt in range(max_retries):
        if is_port_open(host, port):
            try:
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
                    timeout=5,
                )
                if result.returncode == 0:
                    log("PostgreSQL is ready")
                    return
            except subprocess.TimeoutExpired:
                pass
        if attempt < max_retries - 1:
            time.sleep(delay)
    raise RuntimeError(
        f"PostgreSQL not ready after {max_retries} attempts ({max_retries * delay}s)"
    )


def run_bootstrap() -> None:
    """Run the CI bootstrap script to set up the database with timeout."""
    log(f"Running CI bootstrap (timeout: {TIMEOUT_BOOTSTRAP}s)...")

    env = os.environ.copy()
    env["IDIS_PG_HOST"] = "127.0.0.1"
    env["IDIS_PG_PORT"] = str(POSTGRES_PORT)
    env["IDIS_PG_DB_NAME"] = TEST_DB
    env["PG_ADMIN_USER"] = POSTGRES_USER
    env["PG_ADMIN_PASSWORD"] = POSTGRES_PASSWORD
    env["IDIS_PG_APP_USER"] = APP_USER
    env["IDIS_PG_APP_PASSWORD"] = APP_PASSWORD

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        result = subprocess.run(
            [sys.executable, "scripts/pg_bootstrap_ci.py"],
            env=env,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_BOOTSTRAP,
        )
        output = f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        write_artifact("bootstrap.log", output)

        if result.returncode != 0:
            log(f"Bootstrap STDERR: {result.stderr}")
            raise RuntimeError(f"Bootstrap failed with exit code {result.returncode}")
        log("Bootstrap completed successfully")
    except subprocess.TimeoutExpired as err:
        raise RuntimeError(f"Bootstrap timed out after {TIMEOUT_BOOTSTRAP}s") from err


def run_integration_tests() -> int:
    """Run the Postgres integration tests and return the exit code with timeout."""
    log(f"Running Postgres integration tests (timeout: {TIMEOUT_TESTS}s)...")
    log("IDIS_REQUIRE_POSTGRES=1 (tests will NOT be skipped)")

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
        "tests/test_postgres_break_attempts.py",
    ]

    log(f"Test files: {', '.join(test_files)}")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=short"] + test_files,
            env=env,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_TESTS,
        )
        output = f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        write_artifact("pytest.log", output)

        print(result.stdout, flush=True)
        if result.stderr:
            print(result.stderr, file=sys.stderr, flush=True)

        return result.returncode
    except subprocess.TimeoutExpired:
        log(f"ERROR: Tests timed out after {TIMEOUT_TESTS}s")
        return 1


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

    if not check_docker_health():
        log("ERROR: Docker is not available. Please install and start Docker.")
        return 2

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
