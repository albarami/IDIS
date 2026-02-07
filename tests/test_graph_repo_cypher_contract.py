"""Tests for graph repository Cypher query contracts.

Validates each §4.4 query builder:
- tenant_id parameter is present
- Query text contains tenant constraint early
- Deterministic ordering clauses exist where required
- Required parameters are correctly enforced
"""

from __future__ import annotations

from idis.persistence.cypher.q_4_4_1_full_chain import (
    QUERY as Q_4_4_1,
    REQUIRED_PARAMS as RP_4_4_1,
    build_full_chain_query,
)
from idis.persistence.cypher.q_4_4_2_deal_claims_grades import (
    QUERY as Q_4_4_2,
    REQUIRED_PARAMS as RP_4_4_2,
    build_deal_claims_grades_query,
)
from idis.persistence.cypher.q_4_4_3_independence_clusters import (
    QUERY as Q_4_4_3,
    REQUIRED_PARAMS as RP_4_4_3,
    build_independence_clusters_query,
)
from idis.persistence.cypher.q_4_4_4_weakest_link import (
    QUERY as Q_4_4_4,
    REQUIRED_PARAMS as RP_4_4_4,
    build_weakest_link_query,
)
from idis.persistence.cypher.q_4_4_5_defect_impact import (
    QUERY as Q_4_4_5,
    REQUIRED_PARAMS as RP_4_4_5,
    build_defect_impact_query,
)
from idis.persistence.cypher.q_4_4_6_entity_cooccurrence import (
    QUERY as Q_4_4_6,
    REQUIRED_PARAMS as RP_4_4_6,
    build_entity_cooccurrence_query,
)

TENANT_A = "00000000-0000-0000-0000-000000000001"
CLAIM_A = "11111111-1111-1111-1111-111111111111"
DEAL_A = "22222222-2222-2222-2222-222222222222"
DEFECT_A = "33333333-3333-3333-3333-333333333333"


class TestQuery441FullChain:
    """§4.4.1 Full Sanad Chain query contract."""

    def test_tenant_id_in_required_params(self) -> None:
        assert "tenant_id" in RP_4_4_1

    def test_claim_id_in_required_params(self) -> None:
        assert "claim_id" in RP_4_4_1

    def test_query_contains_tenant_constraint(self) -> None:
        assert "$tenant_id" in Q_4_4_1

    def test_tenant_constraint_before_return(self) -> None:
        tenant_pos = Q_4_4_1.index("$tenant_id")
        return_pos = Q_4_4_1.index("RETURN")
        assert tenant_pos < return_pos

    def test_deterministic_ordering(self) -> None:
        assert "ORDER BY tn.timestamp ASC" in Q_4_4_1

    def test_builder_returns_query_and_params(self) -> None:
        query, params = build_full_chain_query(
            claim_id=CLAIM_A, tenant_id=TENANT_A
        )
        assert query == Q_4_4_1
        assert params["tenant_id"] == TENANT_A
        assert params["claim_id"] == CLAIM_A


class TestQuery442DealClaimsGrades:
    """§4.4.2 Deal Claims with Grades query contract."""

    def test_tenant_id_in_required_params(self) -> None:
        assert "tenant_id" in RP_4_4_2

    def test_deal_id_in_required_params(self) -> None:
        assert "deal_id" in RP_4_4_2

    def test_query_contains_tenant_constraint(self) -> None:
        assert "$tenant_id" in Q_4_4_2

    def test_tenant_constraint_in_first_match(self) -> None:
        first_match_end = Q_4_4_2.index("OPTIONAL MATCH")
        assert "$tenant_id" in Q_4_4_2[:first_match_end]

    def test_deterministic_ordering(self) -> None:
        assert "ORDER BY claim.materiality DESC, claim.claim_grade ASC" in Q_4_4_2

    def test_builder_returns_query_and_params(self) -> None:
        query, params = build_deal_claims_grades_query(
            deal_id=DEAL_A, tenant_id=TENANT_A
        )
        assert query == Q_4_4_2
        assert params["tenant_id"] == TENANT_A
        assert params["deal_id"] == DEAL_A


class TestQuery443IndependenceClusters:
    """§4.4.3 Independence Clusters query contract."""

    def test_tenant_id_in_required_params(self) -> None:
        assert "tenant_id" in RP_4_4_3

    def test_claim_id_in_required_params(self) -> None:
        assert "claim_id" in RP_4_4_3

    def test_query_contains_tenant_constraint(self) -> None:
        assert "$tenant_id" in Q_4_4_3

    def test_tenant_constraint_in_first_match(self) -> None:
        first_with = Q_4_4_3.index("WITH")
        assert "$tenant_id" in Q_4_4_3[:first_with]

    def test_corroboration_status_logic(self) -> None:
        assert "MUTAWATIR" in Q_4_4_3
        assert "AHAD_2" in Q_4_4_3
        assert "AHAD_1" in Q_4_4_3
        assert "NONE" in Q_4_4_3

    def test_builder_returns_query_and_params(self) -> None:
        query, params = build_independence_clusters_query(
            claim_id=CLAIM_A, tenant_id=TENANT_A
        )
        assert query == Q_4_4_3
        assert params["tenant_id"] == TENANT_A
        assert params["claim_id"] == CLAIM_A


class TestQuery444WeakestLink:
    """§4.4.4 Weakest Link query contract."""

    def test_tenant_id_in_required_params(self) -> None:
        assert "tenant_id" in RP_4_4_4

    def test_claim_id_in_required_params(self) -> None:
        assert "claim_id" in RP_4_4_4

    def test_query_contains_tenant_constraint(self) -> None:
        assert "$tenant_id" in Q_4_4_4

    def test_deterministic_ordering(self) -> None:
        assert "ORDER BY grade_rank DESC" in Q_4_4_4

    def test_limit_one(self) -> None:
        assert "LIMIT 1" in Q_4_4_4

    def test_grade_ranking_logic(self) -> None:
        assert "WHEN 'A' THEN 0" in Q_4_4_4
        assert "WHEN 'D' THEN 3" in Q_4_4_4

    def test_builder_returns_query_and_params(self) -> None:
        query, params = build_weakest_link_query(
            claim_id=CLAIM_A, tenant_id=TENANT_A
        )
        assert query == Q_4_4_4
        assert params["tenant_id"] == TENANT_A


class TestQuery445DefectImpact:
    """§4.4.5 Defect Impact Analysis query contract."""

    def test_tenant_id_in_required_params(self) -> None:
        assert "tenant_id" in RP_4_4_5

    def test_defect_id_in_required_params(self) -> None:
        assert "defect_id" in RP_4_4_5

    def test_query_contains_tenant_constraint(self) -> None:
        assert "$tenant_id" in Q_4_4_5

    def test_tenant_constraint_in_first_match(self) -> None:
        first_optional = Q_4_4_5.index("OPTIONAL MATCH")
        assert "$tenant_id" in Q_4_4_5[:first_optional]

    def test_includes_downstream_calculations(self) -> None:
        assert "Calculation" in Q_4_4_5
        assert "DERIVED_FROM" in Q_4_4_5

    def test_builder_returns_query_and_params(self) -> None:
        query, params = build_defect_impact_query(
            defect_id=DEFECT_A, tenant_id=TENANT_A
        )
        assert query == Q_4_4_5
        assert params["tenant_id"] == TENANT_A
        assert params["defect_id"] == DEFECT_A


class TestQuery446EntityCooccurrence:
    """§4.4.6 Entity Co-occurrence query contract."""

    def test_tenant_id_in_required_params(self) -> None:
        assert "tenant_id" in RP_4_4_6

    def test_deal_id_in_required_params(self) -> None:
        assert "deal_id" in RP_4_4_6

    def test_query_contains_tenant_constraint(self) -> None:
        assert "$tenant_id" in Q_4_4_6

    def test_deterministic_ordering(self) -> None:
        assert "ORDER BY doc_count DESC" in Q_4_4_6

    def test_minimum_doc_count_filter(self) -> None:
        assert "doc_count >= 2" in Q_4_4_6

    def test_builder_returns_query_and_params(self) -> None:
        query, params = build_entity_cooccurrence_query(
            deal_id=DEAL_A, tenant_id=TENANT_A
        )
        assert query == Q_4_4_6
        assert params["tenant_id"] == TENANT_A
        assert params["deal_id"] == DEAL_A


class TestAllQueriesHaveTenantConstraint:
    """Cross-cutting: every §4.4 query must have $tenant_id."""

    def test_all_queries_contain_tenant_id_param(self) -> None:
        queries = [Q_4_4_1, Q_4_4_2, Q_4_4_3, Q_4_4_4, Q_4_4_5, Q_4_4_6]
        for i, q in enumerate(queries, start=1):
            assert "$tenant_id" in q, f"§4.4.{i} query missing $tenant_id"

    def test_all_required_params_include_tenant_id(self) -> None:
        all_rps = [RP_4_4_1, RP_4_4_2, RP_4_4_3, RP_4_4_4, RP_4_4_5, RP_4_4_6]
        for i, rp in enumerate(all_rps, start=1):
            assert "tenant_id" in rp, f"§4.4.{i} missing tenant_id in REQUIRED_PARAMS"
