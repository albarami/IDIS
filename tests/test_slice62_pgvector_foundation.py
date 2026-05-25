"""Slice 62 pgvector foundation tests — migration, repository, embedding health."""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Generator
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy import Engine

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

TENANT_A_ID = "11111111-1111-1111-1111-111111111111"
TENANT_B_ID = "22222222-2222-2222-2222-222222222222"
DEAL_A_ID = "33333333-3333-3333-3333-333333333333"

EMBEDDING_DIM = 1536


def _skip_or_fail_if_no_postgres() -> None:
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require_postgres = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"
    if not admin_url or not app_url:
        msg = f"PostgreSQL integration tests require {ADMIN_URL_ENV} and {APP_URL_ENV}"
        if require_postgres:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        pytest.skip(msg)


def _pgvector_extension_available(admin_engine: Engine) -> bool:
    try:
        with admin_engine.begin() as conn:
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector"'))
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def admin_engine() -> Generator[Engine, None, None]:
    _skip_or_fail_if_no_postgres()
    from idis.persistence.db import get_admin_engine, reset_engines

    engine = get_admin_engine()
    yield engine
    reset_engines()


@pytest.fixture(scope="module")
def app_engine() -> Generator[Engine, None, None]:
    _skip_or_fail_if_no_postgres()
    from idis.persistence.db import get_app_engine, reset_engines

    engine = get_app_engine()
    yield engine
    reset_engines()


@pytest.fixture(scope="module")
def migrated_db(admin_engine: Engine) -> Generator[None, None, None]:
    if not _pgvector_extension_available(admin_engine):
        pytest.skip("pgvector extension is not available on this PostgreSQL instance")

    from alembic import command
    from alembic.config import Config

    import idis.persistence.migrations as migrations_pkg

    config = Config()
    config.set_main_option("script_location", os.path.dirname(migrations_pkg.__file__))
    with admin_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")

    yield

    with admin_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, "base")


@pytest.fixture
def clean_vector_tables(admin_engine: Engine, migrated_db: None) -> Generator[None, None, None]:
    with admin_engine.begin() as conn:
        conn.execute(text("TRUNCATE vector_embeddings, deals CASCADE"))
        for tenant_id, deal_id in (
            (TENANT_A_ID, DEAL_A_ID),
            (TENANT_B_ID, DEAL_A_ID),
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO deals (deal_id, tenant_id, name, status, created_at, updated_at)
                    VALUES (:deal_id, :tenant_id, :name, 'ACTIVE', now(), now())
                    ON CONFLICT (deal_id) DO NOTHING
                    """
                ),
                {"deal_id": deal_id, "tenant_id": tenant_id, "name": "vector-test-deal"},
            )
    yield


def test_migration_schema_dimension_matches_runtime_constant() -> None:
    from pathlib import Path

    from idis.services.rag.constants import VECTOR_EMBEDDING_DIMENSIONS

    migration_path = (
        Path(__file__).resolve().parents[1]
        / "src/idis/persistence/migrations/versions/0017_vector_embeddings.py"
    )
    migration_text = migration_path.read_text(encoding="utf-8")

    assert "from idis." not in migration_text
    match = re.search(r"VECTOR_EMBEDDING_DIMENSIONS\s*=\s*(\d+)", migration_text)
    assert match is not None
    assert int(match.group(1)) == VECTOR_EMBEDDING_DIMENSIONS
    assert "embedding vector({VECTOR_EMBEDDING_DIMENSIONS})" in migration_text


def test_migration_creates_vector_embeddings_table_with_rls(
    admin_engine: Engine,
    migrated_db: None,
) -> None:
    with admin_engine.connect() as conn:
        table = conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'vector_embeddings'
                """
            )
        ).scalar_one()
        assert table == 1

        rls = conn.execute(
            text(
                """
                SELECT relrowsecurity, relforcerowsecurity
                FROM pg_class
                WHERE relname = 'vector_embeddings'
                """
            )
        ).one()
        assert rls[0] is True
        assert rls[1] is True


def test_embedding_health_missing_env_reports_missing_credentials() -> None:
    from idis.services.rag.embedding_health import (
        EmbeddingHealthStatus,
        check_embedding_health,
    )

    result = check_embedding_health(env={})

    assert result.status == EmbeddingHealthStatus.MISSING_CREDENTIALS
    assert "IDIS_ENABLE_VECTOR_SEARCH" in result.missing_env_vars
    assert "OPENAI_API_KEY" in result.missing_env_vars
    assert result.error is None or "secret" not in (result.error or "").lower()


def test_embedding_health_rejects_deterministic_backend() -> None:
    from idis.services.rag.embedding_health import (
        EmbeddingHealthStatus,
        check_embedding_health,
    )

    result = check_embedding_health(
        env={
            "IDIS_ENABLE_VECTOR_SEARCH": "true",
            "IDIS_EMBEDDING_BACKEND": "deterministic",
            "OPENAI_API_KEY": "sk-test",
            "IDIS_EMBEDDING_MODEL": "text-embedding-3-small",
            "IDIS_EMBEDDING_DIMENSIONS": "1536",
        }
    )

    assert result.status == EmbeddingHealthStatus.FAILED
    assert result.error is not None
    assert "deterministic" in result.error.lower()


def test_embedding_health_invalid_dimensions_returns_failed_not_exception() -> None:
    from idis.services.rag.embedding_health import (
        EmbeddingHealthStatus,
        check_embedding_health,
    )

    result = check_embedding_health(
        env={
            "IDIS_ENABLE_VECTOR_SEARCH": "true",
            "IDIS_EMBEDDING_BACKEND": "openai",
            "OPENAI_API_KEY": "sk-test",
            "IDIS_EMBEDDING_MODEL": "text-embedding-3-small",
            "IDIS_EMBEDDING_DIMENSIONS": "not-an-int",
        }
    )

    assert result.status == EmbeddingHealthStatus.FAILED
    assert "IDIS_EMBEDDING_DIMENSIONS" in (result.error or "")


def test_embedding_health_rejects_non_schema_dimensions() -> None:
    from idis.services.rag.embedding_health import (
        EmbeddingHealthStatus,
        check_embedding_health,
    )

    result = check_embedding_health(
        env={
            "IDIS_ENABLE_VECTOR_SEARCH": "true",
            "IDIS_EMBEDDING_BACKEND": "openai",
            "OPENAI_API_KEY": "sk-test",
            "IDIS_EMBEDDING_MODEL": "text-embedding-3-small",
            "IDIS_EMBEDDING_DIMENSIONS": "512",
        }
    )

    assert result.status == EmbeddingHealthStatus.FAILED
    assert "1536" in (result.error or "")


def test_embedding_health_unsupported_backend_error_is_sanitized() -> None:
    from idis.services.rag.embedding_health import (
        EmbeddingHealthStatus,
        check_embedding_health,
    )

    result = check_embedding_health(
        env={
            "IDIS_ENABLE_VECTOR_SEARCH": "true",
            "IDIS_EMBEDDING_BACKEND": r"c:\secret\backend",
            "OPENAI_API_KEY": "sk-test",
            "IDIS_EMBEDDING_MODEL": "text-embedding-3-small",
            "IDIS_EMBEDDING_DIMENSIONS": "1536",
        }
    )

    assert result.status == EmbeddingHealthStatus.FAILED
    assert result.error == "Unsupported embedding backend. Allowed backend: openai."
    assert "secret" not in (result.error or "").lower()


def test_embedding_health_success_with_injected_client() -> None:
    from idis.services.rag.embedding_health import (
        EmbeddingHealthStatus,
        check_embedding_health,
    )

    class _FakeEmbeddings:
        def create(self, **kwargs: Any) -> Any:
            assert kwargs["model"] == "text-embedding-3-small"
            assert kwargs["input"] == "idis-embedding-health-check"
            data = MagicMock()
            data.embedding = [0.1] * EMBEDDING_DIM
            response = MagicMock()
            response.data = [data]
            return response

    class _FakeClient:
        embeddings = _FakeEmbeddings()

    result = check_embedding_health(
        env={
            "IDIS_ENABLE_VECTOR_SEARCH": "true",
            "IDIS_EMBEDDING_BACKEND": "openai",
            "OPENAI_API_KEY": "sk-test-not-real",
            "IDIS_EMBEDDING_MODEL": "text-embedding-3-small",
            "IDIS_EMBEDDING_DIMENSIONS": "1536",
        },
        client_factory=lambda _api_key: _FakeClient(),
    )

    assert result.status == EmbeddingHealthStatus.HEALTHY
    assert result.model == "text-embedding-3-small"
    assert result.dimensions == EMBEDDING_DIM
    dumped = json.dumps(result.model_dump())
    assert "sk-test" not in dumped


def test_embedding_health_failure_is_sanitized() -> None:
    from idis.services.rag.embedding_health import (
        EmbeddingHealthStatus,
        check_embedding_health,
    )

    class _BrokenEmbeddings:
        def create(self, **kwargs: Any) -> Any:
            raise RuntimeError("401 invalid_api_key sk-super-secret-key-leak")

    class _BrokenClient:
        embeddings = _BrokenEmbeddings()

    result = check_embedding_health(
        env={
            "IDIS_ENABLE_VECTOR_SEARCH": "1",
            "IDIS_EMBEDDING_BACKEND": "openai",
            "OPENAI_API_KEY": "sk-super-secret-key-leak",
            "IDIS_EMBEDDING_MODEL": "text-embedding-3-small",
            "IDIS_EMBEDDING_DIMENSIONS": "1536",
        },
        client_factory=lambda _api_key: _BrokenClient(),
    )

    assert result.status == EmbeddingHealthStatus.FAILED
    assert "sk-super-secret" not in (result.error or "")


def test_pgvector_health_requires_database_url() -> None:
    from idis.services.rag.pgvector_health import (
        PgvectorHealthStatus,
        check_pgvector_health,
    )

    result = check_pgvector_health(env={})

    assert result.status == PgvectorHealthStatus.MISSING_CREDENTIALS
    assert "IDIS_DATABASE_URL" in result.missing_env_vars


def test_pgvector_health_success_with_injected_probe() -> None:
    from idis.services.rag.pgvector_health import (
        PgvectorHealthStatus,
        check_pgvector_health,
    )

    result = check_pgvector_health(
        env={"IDIS_DATABASE_URL": "postgresql://user:pass@localhost/db"},
        extension_probe=lambda _url: True,
    )

    assert result.status == PgvectorHealthStatus.HEALTHY


def test_vector_repository_rejects_wrong_dimensions(app_engine: Engine, migrated_db: None) -> None:
    from idis.persistence.repositories.vector_embeddings import PostgresVectorEmbeddingsRepository

    with app_engine.connect() as conn, conn.begin():
        repo = PostgresVectorEmbeddingsRepository(conn, TENANT_A_ID)
        with pytest.raises(ValueError, match="dimensions"):
            repo.upsert_embedding(
                deal_id=DEAL_A_ID,
                source_type="document_span",
                source_id=str(uuid.uuid4()),
                content_hash="hash-1",
                embedding=[0.1, 0.2],
                embedding_model="text-embedding-3-small",
                embedding_dimensions=EMBEDDING_DIM,
            )


def test_vector_repository_upsert_and_similarity_search(
    app_engine: Engine,
    migrated_db: None,
    clean_vector_tables: None,
) -> None:
    from idis.persistence.db import set_tenant_local
    from idis.persistence.repositories.vector_embeddings import PostgresVectorEmbeddingsRepository

    source_id = str(uuid.uuid4())
    base_vector = [0.0] * EMBEDDING_DIM
    base_vector[0] = 1.0
    near_vector = [0.0] * EMBEDDING_DIM
    near_vector[0] = 0.99
    far_vector = [0.0] * EMBEDDING_DIM
    far_vector[1] = 1.0

    with app_engine.connect() as conn, conn.begin():
        set_tenant_local(conn, TENANT_A_ID)
        repo = PostgresVectorEmbeddingsRepository(conn, TENANT_A_ID)
        repo.upsert_embedding(
            deal_id=DEAL_A_ID,
            source_type="document_span",
            source_id=source_id,
            content_hash="hash-base",
            embedding=base_vector,
            embedding_model="text-embedding-3-small",
            embedding_dimensions=EMBEDDING_DIM,
        )
        repo.upsert_embedding(
            deal_id=DEAL_A_ID,
            source_type="document_span",
            source_id=str(uuid.uuid4()),
            content_hash="hash-near",
            embedding=near_vector,
            embedding_model="text-embedding-3-small",
            embedding_dimensions=EMBEDDING_DIM,
        )
        repo.upsert_embedding(
            deal_id=DEAL_A_ID,
            source_type="document_span",
            source_id=str(uuid.uuid4()),
            content_hash="hash-far",
            embedding=far_vector,
            embedding_model="text-embedding-3-small",
            embedding_dimensions=EMBEDDING_DIM,
        )

        results = repo.similarity_search(
            deal_id=DEAL_A_ID,
            query_embedding=base_vector,
            limit=2,
        )

    assert len(results) == 2
    assert results[0]["source_id"] == source_id
    assert results[0]["score"] >= results[1]["score"]
    assert "text_excerpt" not in results[0]
    assert "embedding" not in results[0]


def test_vector_repository_isolates_tenants_under_rls(
    app_engine: Engine,
    migrated_db: None,
    clean_vector_tables: None,
) -> None:
    from idis.persistence.db import set_tenant_local
    from idis.persistence.repositories.vector_embeddings import PostgresVectorEmbeddingsRepository

    vector = [0.0] * EMBEDDING_DIM
    vector[0] = 1.0
    tenant_b_source = str(uuid.uuid4())

    with app_engine.connect() as conn, conn.begin():
        set_tenant_local(conn, TENANT_B_ID)
        repo_b = PostgresVectorEmbeddingsRepository(conn, TENANT_B_ID)
        repo_b.upsert_embedding(
            deal_id=DEAL_A_ID,
            source_type="document_span",
            source_id=tenant_b_source,
            content_hash="tenant-b-only",
            embedding=vector,
            embedding_model="text-embedding-3-small",
            embedding_dimensions=EMBEDDING_DIM,
        )

    with app_engine.connect() as conn, conn.begin():
        set_tenant_local(conn, TENANT_A_ID)
        repo_a = PostgresVectorEmbeddingsRepository(conn, TENANT_A_ID)
        results = repo_a.similarity_search(
            deal_id=DEAL_A_ID,
            query_embedding=vector,
            limit=5,
        )

    assert results == []


def test_vector_repository_upsert_conflict_returns_existing_embedding_id(
    app_engine: Engine,
    migrated_db: None,
    clean_vector_tables: None,
) -> None:
    from idis.persistence.db import set_tenant_local
    from idis.persistence.repositories.vector_embeddings import PostgresVectorEmbeddingsRepository

    source_id = str(uuid.uuid4())
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = 1.0
    updated_vector = [0.0] * EMBEDDING_DIM
    updated_vector[0] = 0.5

    with app_engine.connect() as conn, conn.begin():
        set_tenant_local(conn, TENANT_A_ID)
        repo = PostgresVectorEmbeddingsRepository(conn, TENANT_A_ID)
        first = repo.upsert_embedding(
            deal_id=DEAL_A_ID,
            source_type="document_span",
            source_id=source_id,
            content_hash="same-hash",
            embedding=vector,
            embedding_model="text-embedding-3-small",
            embedding_dimensions=EMBEDDING_DIM,
        )
        second = repo.upsert_embedding(
            deal_id=DEAL_A_ID,
            source_type="document_span",
            source_id=source_id,
            content_hash="same-hash",
            embedding=updated_vector,
            embedding_model="text-embedding-3-small",
            embedding_dimensions=EMBEDDING_DIM,
        )

    assert first["embedding_id"] == second["embedding_id"]


def test_strict_inventory_pgvector_rag_exists_but_not_full_wired() -> None:
    from idis.services.rag.embedding_health import EmbeddingHealthCheck
    from idis.services.rag.pgvector_health import PgvectorHealthCheck
    from idis.services.runs.strict_full_live import build_strict_full_live_readiness_report

    report = build_strict_full_live_readiness_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://user:pass@localhost/db",
            "IDIS_ENABLE_VECTOR_SEARCH": "true",
            "IDIS_EMBEDDING_BACKEND": "openai",
            "OPENAI_API_KEY": "sk-test",
            "IDIS_EMBEDDING_MODEL": "text-embedding-3-small",
            "IDIS_EMBEDDING_DIMENSIONS": "1536",
        },
        embedding_health_checker=lambda _env: EmbeddingHealthCheck.healthy(
            model="text-embedding-3-small",
            dimensions=1536,
        ),
        pgvector_health_checker=lambda _env: PgvectorHealthCheck.healthy(),
    )
    inventory = {item.component_name: item for item in report.component_inventory}

    rag = inventory["pgvector/RAG"]
    assert rag.exists_in_code is True
    assert rag.full_wired is False
    assert rag.output_visible is False
    assert rag.health_check_status == "healthy"
    assert "FULL" in rag.blocker or "index" in rag.blocker.lower()

    rag_component = report.component("rag_evidence_retrieval")
    assert rag_component.status.value == "code-exists-but-not-wired"
    assert rag_component.may_proceed is False


def test_strict_inventory_rejects_non_schema_embedding_dimensions() -> None:
    from idis.services.rag.pgvector_health import PgvectorHealthCheck
    from idis.services.runs.strict_full_live import build_strict_full_live_readiness_report

    report = build_strict_full_live_readiness_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://user:pass@localhost/db",
            "IDIS_ENABLE_VECTOR_SEARCH": "true",
            "IDIS_EMBEDDING_BACKEND": "openai",
            "OPENAI_API_KEY": "sk-test",
            "IDIS_EMBEDDING_MODEL": "text-embedding-3-small",
            "IDIS_EMBEDDING_DIMENSIONS": "512",
        },
        pgvector_health_checker=lambda _env: PgvectorHealthCheck.healthy(),
    )
    inventory = {item.component_name: item for item in report.component_inventory}

    assert inventory["pgvector/RAG"].health_check_status == "configured_failed"
    assert report.component("rag_evidence_retrieval").may_proceed is False


def test_strict_inventory_supabase_vectors_uses_postgres_not_sdk() -> None:
    from idis.services.runs.strict_full_live import build_strict_full_live_readiness_report

    report = build_strict_full_live_readiness_report(
        env={
            "IDIS_DATABASE_URL": "postgresql://user:pass@db.supabase.co:5432/postgres",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_KEY": "present-label-only",
        }
    )
    inventory = {item.component_name: item for item in report.component_inventory}
    supabase_vectors = inventory["Supabase Vectors/RAG"]

    assert supabase_vectors.exists_in_code is True
    assert supabase_vectors.full_wired is False
    assert "SDK" in supabase_vectors.blocker or "Postgres" in supabase_vectors.blocker
    dumped = json.dumps(supabase_vectors.model_dump())
    assert "present-label-only" not in dumped


def test_audit_rag_vector_retrieval_reports_foundation_gap() -> None:
    from pathlib import Path

    from scripts.audit_full_system_wiring import collect_wiring_inventory

    inventory = collect_wiring_inventory(Path(__file__).resolve().parents[1])
    item = inventory["rag_vector_retrieval"]

    assert item.status == "PARTIAL"
    assert any("0017" in evidence or "vector_embeddings" in evidence for evidence in item.evidence)
    assert any("FULL" in gap or "index" in gap.lower() for gap in item.gaps)
