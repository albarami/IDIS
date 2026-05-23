"""Secret-free dotenv loading and config inventory for strict full-live."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.services.runs.strict_full_live_health import StrictHealthCheckResult
from idis.services.runs.strict_full_live_models import StrictEnvVarInventory

ANTHROPIC_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "IDIS_EXTRACT_BACKEND",
    "IDIS_DEBATE_BACKEND",
    "IDIS_ANTHROPIC_MODEL_EXTRACT",
    "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT",
    "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER",
)
RUNTIME_ENV_VARS = (
    "IDIS_DATABASE_URL",
    "IDIS_DATABASE_ADMIN_URL",
    IDIS_API_KEYS_ENV,
    "IDIS_OBJECT_STORE_BACKEND",
)
RUNTIME_HEALTH_CHECKED_ENV_VARS = (
    "IDIS_DATABASE_URL",
    IDIS_API_KEYS_ENV,
    "IDIS_OBJECT_STORE_BACKEND",
)
NEO4J_ENV_VARS = ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")
SUPABASE_ENV_VARS = ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SECRET_KEY")
BYOL_PROVIDER_ENV_VARS = (
    "COMPANIES_HOUSE_API_KEY",
    "FRED_API_KEY",
    "FINNHUB_API_KEY",
    "FMP_API_KEY",
    "GITHUB_API_TOKEN",
)
STRICT_INVENTORY_ENV_VARS = (
    *ANTHROPIC_ENV_VARS,
    *RUNTIME_ENV_VARS,
    *NEO4J_ENV_VARS,
    *SUPABASE_ENV_VARS,
    *BYOL_PROVIDER_ENV_VARS,
)


@dataclass(frozen=True, slots=True)
class StrictEnvSource:
    """Effective strict env plus source key sets, without exposing values."""

    effective_env: dict[str, str]
    process_keys: frozenset[str]
    dotenv_keys: frozenset[str]


def build_strict_env_source(
    *,
    process_env: Mapping[str, str],
    dotenv_path: str | Path | None,
) -> StrictEnvSource:
    """Build effective env from process env over optional dotenv values."""
    dotenv_values = parse_dotenv_values(dotenv_path)
    effective_env = dict(dotenv_values)
    effective_env.update({key: str(value) for key, value in process_env.items()})
    return StrictEnvSource(
        effective_env=effective_env,
        process_keys=frozenset(process_env.keys()),
        dotenv_keys=frozenset(dotenv_values.keys()),
    )


def parse_dotenv_values(dotenv_path: str | Path | None) -> dict[str, str]:
    """Parse dotenv values for local strict probes without logging them."""
    if dotenv_path is None:
        return {}
    path = Path(dotenv_path)
    if not path.exists() or not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_dotenv_line(line)
        if parsed is not None:
            key, value = parsed
            values[key] = value
    return values


def build_env_config_inventory(
    *,
    env_source: StrictEnvSource,
    llm_health: StrictHealthCheckResult | None,
    runtime_health: StrictHealthCheckResult | None,
) -> list[StrictEnvVarInventory]:
    """Build a secret-free config propagation inventory."""
    return [
        StrictEnvVarInventory(
            env_var=env_var,
            present_in_dotenv=env_var in env_source.dotenv_keys,
            loaded_in_process=env_var in env_source.process_keys,
            read_by_code=_is_read_by_code(env_var),
            wired_into_full=_is_wired_into_full(env_var),
            health_checked_live=_is_health_checked(env_var, llm_health, runtime_health),
            note=_inventory_note(env_var),
        )
        for env_var in STRICT_INVENTORY_ENV_VARS
        if env_var in env_source.dotenv_keys or env_var in env_source.process_keys
    ]


def find_nearest_dotenv(start: str | Path) -> Path | None:
    """Find the nearest .env at or above a path without reading it."""
    current = Path(start).resolve()
    if current.is_file():
        current = current.parent
    for candidate_dir in (current, *current.parents):
        candidate = candidate_dir / ".env"
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def has_supabase_config(env: Mapping[str, str]) -> bool:
    """Return whether any Supabase config is present."""
    return any(_has_value(env, key) for key in SUPABASE_ENV_VARS) or _database_url_is_supabase(env)


def database_url_is_supabase(env: Mapping[str, str]) -> bool:
    """Return whether the generic Postgres URL appears to target Supabase."""
    return _database_url_is_supabase(env)


def present_byol_provider_env_vars(env: Mapping[str, str]) -> list[str]:
    """Return BYOL provider env names present in effective config."""
    return [key for key in BYOL_PROVIDER_ENV_VARS if _has_value(env, key)]


def _parse_dotenv_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if key.startswith("export "):
        key = key.removeprefix("export ").strip()
    if not key:
        return None
    return key, _strip_dotenv_value(value.strip())


def _strip_dotenv_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    if " #" in value:
        return value.split(" #", 1)[0].rstrip()
    return value


def _is_read_by_code(env_var: str) -> bool:
    if env_var in ANTHROPIC_ENV_VARS or env_var in RUNTIME_ENV_VARS:
        return True
    return env_var in NEO4J_ENV_VARS


def _is_wired_into_full(env_var: str) -> bool:
    return env_var in ANTHROPIC_ENV_VARS or env_var in {"IDIS_DATABASE_URL", IDIS_API_KEYS_ENV}


def _is_health_checked(
    env_var: str,
    llm_health: StrictHealthCheckResult | None,
    runtime_health: StrictHealthCheckResult | None,
) -> bool:
    if env_var in ANTHROPIC_ENV_VARS:
        return llm_health is not None and llm_health.passed
    if env_var in RUNTIME_HEALTH_CHECKED_ENV_VARS:
        return runtime_health is not None and runtime_health.passed
    return False


def _inventory_note(env_var: str) -> str:
    if env_var in BYOL_PROVIDER_ENV_VARS:
        return "present config is not loaded into the tenant BYOL credential repository"
    if env_var in SUPABASE_ENV_VARS:
        return "Supabase product config is not read by FULL runtime paths"
    if env_var in NEO4J_ENV_VARS:
        return "Neo4j code exists, but graph projection is not wired into FULL"
    return "tracked strict full-live config"


def _database_url_is_supabase(env: Mapping[str, str]) -> bool:
    return "supabase" in str(env.get("IDIS_DATABASE_URL", "")).lower()


def _has_value(env: Mapping[str, str], key: str) -> bool:
    return bool(str(env.get(key, "")).strip())
