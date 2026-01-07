# IDIS Engineering Documentation Index (v6.3)

This folder contains the enterprise-grade documentation set required to build IDIS cleanly and safely as a solo builder with AI coding assistants.

## Read Order (recommended)

1) **Canonical Spec**
- IDIS v6.3 FINAL (docx) — the source-of-truth system specification.

2) **Build Specs**
- `IDIS_TDD_v6_3.md` — technical design and architecture translation
- `IDIS_Data_Model_Schemas_v6_3.md` — canonical object models + schemas
- `IDIS_OpenAPI_v6_3.yaml` — OpenAPI contract
- `IDIS_API_and_Integration_Contracts_v6_3.md` — integration rules + idempotency + webhooks

3) **Security & Compliance**
- `IDIS_Security_Threat_Model_v6_3.md`
- `IDIS_Audit_Event_Taxonomy_v6_3.md`
- `IDIS_Data_Residency_and_Compliance_Model_v6_3.md`

4) **Operations**
- `IDIS_SLO_SLA_Runbooks_v6_3.md`

5) **Quality & Safe Iteration**
- `IDIS_Evaluation_Harness_and_Release_Gates_v6_3.md`
- `IDIS_Prompt_Registry_and_Model_Policy_v6_3.md`

6) **Execution & Traceability**
- `10_IDIS_GoLive_Execution_Plan_v6_3.md` — Authoritative phase roadmap with deliverables, gates, and go-live checklist
- `11_IDIS_Traceability_Matrix_v6_3.md` — Requirements → Docs → Code → Tests → Evidence mapping

## Suggested Repo Layout (when you start coding)

- `/docs/` → all documentation
- `/schemas/` → JSON schemas (mirrored from schema doc)
- `/openapi/` → OpenAPI yaml + generated clients
- `/services/` → microservices or modular monolith components
- `/orchestrator/` → LangGraph workflows
- `/calc/` → deterministic calculation engines
- `/ui/` → frontend (Truth Dashboard, Sanad Map)
- `/infra/` → IaC, k8s, Helm, Terraform
- `/tests/` → golden datasets + regression harness

## Operating Discipline (recommended)

- Never change prompts, calc formulas, or validators without passing the evaluation harness gates.
- Treat the No‑Free‑Facts and Muḥāsabah validators as hard blockers.
- Keep audit events immutable; if something goes wrong, add new events—never edit history.

