@echo off
REM Windows-compatible make runner for IDIS project
REM Provides equivalent functionality to GNU Make targets
REM Usage: make [target]  (e.g., make format, make lint, make test, make check)

setlocal enabledelayedexpansion

if "%1"=="" goto help
if "%1"=="help" goto help
if "%1"=="format" goto format
if "%1"=="lint" goto lint
if "%1"=="typecheck" goto typecheck
if "%1"=="test" goto test
if "%1"=="check" goto check
if "%1"=="forbidden_scan" goto forbidden_scan
if "%1"=="postgres_integration" goto postgres_integration

echo Unknown target: %1
goto help

:format
echo === Running: ruff format . ===
ruff format .
if errorlevel 1 exit /b 1
echo === Format complete ===
goto end

:lint
echo === Running: ruff check . ===
ruff check .
if errorlevel 1 exit /b 1
echo === Lint complete ===
goto end

:typecheck
echo === Running: mypy src ===
python -m mypy src
if errorlevel 1 exit /b 1
echo === Typecheck complete ===
goto end

:test
echo === Running: pytest ===
python -m pytest
if errorlevel 1 exit /b 1
echo === Tests complete ===
goto end

:forbidden_scan
echo === Running: python scripts/forbidden_scan.py ===
python scripts/forbidden_scan.py
if errorlevel 1 exit /b 1
echo === Forbidden scan complete ===
goto end

:postgres_integration
echo === Running: python scripts/run_postgres_integration_local.py ===
python scripts/run_postgres_integration_local.py
if errorlevel 1 exit /b 1
echo === Postgres integration tests complete ===
goto end

:check
echo === Running full check: format + lint + typecheck + test + forbidden_scan ===
echo.
echo --- Step 1/5: Format ---
call :format_internal
if errorlevel 1 exit /b 1

echo --- Step 2/5: Lint ---
call :lint_internal
if errorlevel 1 exit /b 1

echo --- Step 3/5: Typecheck ---
call :typecheck_internal
if errorlevel 1 exit /b 1

echo --- Step 4/5: Test ---
call :test_internal
if errorlevel 1 exit /b 1

echo --- Step 5/5: Forbidden Scan ---
call :forbidden_scan_internal
if errorlevel 1 exit /b 1

echo.
echo === All checks passed ===
goto end

:format_internal
ruff format .
exit /b %errorlevel%

:lint_internal
ruff check .
exit /b %errorlevel%

:typecheck_internal
python -m mypy src
exit /b %errorlevel%

:test_internal
python -m pytest
exit /b %errorlevel%

:forbidden_scan_internal
python scripts/forbidden_scan.py
exit /b %errorlevel%

:help
echo IDIS Project - Windows Make Runner
echo.
echo Usage: make [target]
echo.
echo Available targets:
echo   format        Run ruff format on codebase
echo   lint          Run ruff check on codebase
echo   typecheck     Run mypy type checking
echo   test          Run pytest test suite
echo   check         Run all checks (format + lint + typecheck + test)
echo   forbidden_scan  Run forbidden pattern scanner
echo   postgres_integration  Run Postgres integration tests locally (requires Docker)
echo   help          Show this help message
echo.
echo On Linux/macOS, use GNU Make with the Makefile.
echo On Windows, this batch file provides equivalent functionality.
goto end

:end
endlocal
