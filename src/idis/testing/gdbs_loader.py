"""
GDBS (Golden Deal Benchmark Suite) Loader.

Production-safe, fail-closed loader for GDBS-FULL dataset.
NO placeholders, NO mocks, NO hardcoded outputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class GDBSLoadError(Exception):
    """Structured error for GDBS loading failures. Fail-closed."""

    def __init__(
        self, message: str, path: Path | None = None, details: dict[str, Any] | None = None
    ) -> None:
        self.message = message
        self.path = path
        self.details = details or {}
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        parts = [self.message]
        if self.path:
            parts.append(f"path={self.path}")
        if self.details:
            parts.append(f"details={self.details}")
        return " | ".join(parts)


@dataclass
class GDBSTenant:
    """Tenant configuration from GDBS dataset."""

    tenant_id: str
    name: str
    data_residency_region: str | None
    raw: dict[str, Any]


@dataclass
class GDBSActor:
    """Actor configuration from GDBS dataset."""

    actor_id: str
    tenant_id: str
    email: str
    display_name: str
    role: str
    raw: dict[str, Any]


@dataclass
class GDBSDeal:
    """Deal with all associated data from GDBS dataset."""

    deal_id: str
    tenant_id: str
    deal_key: str
    scenario: str
    company_name: str
    stage: str
    sector: str
    artifacts: list[dict[str, Any]]
    spans: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    sanads: list[dict[str, Any]]
    defects: list[dict[str, Any]]
    calcs: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class GDBSExpectedOutcome:
    """Expected outcome for a deal."""

    deal_id: str
    deal_key: str
    scenario: str
    expected_claims: dict[str, dict[str, Any]]
    expected_defects: list[dict[str, Any]]
    validation_rules: list[dict[str, Any]]
    raw: dict[str, Any]


@dataclass
class GDBSDataset:
    """Complete GDBS dataset."""

    manifest: dict[str, Any]
    tenant: GDBSTenant
    actors: list[GDBSActor]
    deals: list[GDBSDeal]
    expected_outcomes: dict[str, GDBSExpectedOutcome]
    audit_expectations: dict[str, Any]
    dataset_path: Path

    def get_deal(self, deal_key: str) -> GDBSDeal | None:
        """Get deal by key (e.g., 'deal_001')."""
        for deal in self.deals:
            if deal.deal_key == deal_key:
                return deal
        return None

    def get_deal_by_id(self, deal_id: str) -> GDBSDeal | None:
        """Get deal by UUID."""
        for deal in self.deals:
            if deal.deal_id == deal_id:
                return deal
        return None

    def get_expected_outcome(self, deal_key: str) -> GDBSExpectedOutcome | None:
        """Get expected outcome for a deal."""
        return self.expected_outcomes.get(deal_key)

    def get_deals_by_scenario(self, scenario: str) -> list[GDBSDeal]:
        """Get all deals matching a scenario type."""
        return [d for d in self.deals if d.scenario == scenario]


class GDBSLoader:
    """
    Fail-closed loader for GDBS datasets.

    Validates all required files exist and conform to expected structure.
    Raises GDBSLoadError on any validation failure.
    """

    REQUIRED_FILES = [
        "manifest.json",
        "tenant/tenant_qatar_alpha.json",
        "actors/analyst_1.json",
        "actors/analyst_2.json",
        "actors/admin_1.json",
        "audit_expectations/required_events.json",
    ]

    DEAL_REQUIRED_FILES = [
        "deal.json",
        "artifacts.json",
        "spans.json",
        "claims.json",
        "evidence.json",
        "sanads.json",
        "calcs.json",  # MANDATORY per v6.3 spec - fail-closed if missing
    ]

    def __init__(self, dataset_path: str | Path) -> None:
        self.dataset_path = Path(dataset_path)
        if not self.dataset_path.exists():
            raise GDBSLoadError("Dataset path does not exist", self.dataset_path)
        if not self.dataset_path.is_dir():
            raise GDBSLoadError("Dataset path is not a directory", self.dataset_path)

    def _load_json(self, path: Path) -> dict[str, Any]:
        """Load and parse JSON file. Fail-closed on any error."""
        if not path.exists():
            raise GDBSLoadError("Required file not found", path)
        if not path.is_file():
            raise GDBSLoadError("Path is not a file", path)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise GDBSLoadError("JSON root must be object", path, {"type": type(data).__name__})
            return data
        except json.JSONDecodeError as e:
            raise GDBSLoadError("Invalid JSON", path, {"error": str(e)}) from e

    def _validate_required_fields(
        self, data: dict[str, Any], required: list[str], path: Path
    ) -> None:
        """Validate required fields exist. Fail-closed."""
        missing = [f for f in required if f not in data]
        if missing:
            raise GDBSLoadError("Missing required fields", path, {"missing": missing})

    def _load_tenant(self) -> GDBSTenant:
        """Load tenant configuration."""
        path = self.dataset_path / "tenant" / "tenant_qatar_alpha.json"
        data = self._load_json(path)
        self._validate_required_fields(data, ["tenant_id", "name"], path)
        return GDBSTenant(
            tenant_id=data["tenant_id"],
            name=data["name"],
            data_residency_region=data.get("data_residency_region"),
            raw=data,
        )

    def _load_actors(self) -> list[GDBSActor]:
        """Load all actor configurations."""
        actors = []
        actors_dir = self.dataset_path / "actors"
        if not actors_dir.exists():
            raise GDBSLoadError("Actors directory not found", actors_dir)

        for actor_file in ["analyst_1.json", "analyst_2.json", "admin_1.json"]:
            path = actors_dir / actor_file
            data = self._load_json(path)
            self._validate_required_fields(
                data, ["actor_id", "tenant_id", "email", "display_name", "role"], path
            )
            actors.append(
                GDBSActor(
                    actor_id=data["actor_id"],
                    tenant_id=data["tenant_id"],
                    email=data["email"],
                    display_name=data["display_name"],
                    role=data["role"],
                    raw=data,
                )
            )
        return actors

    def _load_deal(self, deal_dir: Path, deal_key: str) -> GDBSDeal:
        """Load a single deal with all components."""
        if not deal_dir.exists():
            raise GDBSLoadError("Deal directory not found", deal_dir)

        deal_data = self._load_json(deal_dir / "deal.json")
        self._validate_required_fields(
            deal_data, ["deal_id", "tenant_id", "company_name", "scenario"], deal_dir / "deal.json"
        )

        artifacts_data = self._load_json(deal_dir / "artifacts.json")
        spans_data = self._load_json(deal_dir / "spans.json")
        claims_data = self._load_json(deal_dir / "claims.json")
        evidence_data = self._load_json(deal_dir / "evidence.json")
        sanads_data = self._load_json(deal_dir / "sanads.json")

        defects_path = deal_dir / "defects.json"
        defects_data: dict[str, Any] = {"defects": []}
        if defects_path.exists():
            defects_data = self._load_json(defects_path)

        # calcs.json is MANDATORY per v6.3 spec - fail-closed if missing
        calcs_path = deal_dir / "calcs.json"
        if not calcs_path.exists():
            raise GDBSLoadError(
                "Missing mandatory calcs.json - all deals must have deterministic calculations",
                calcs_path,
                {"deal_key": deal_key},
            )
        calcs_data = self._load_json(calcs_path)
        if not calcs_data.get("calc_sanads"):
            raise GDBSLoadError(
                "calcs.json must contain non-empty calc_sanads array",
                calcs_path,
                {"deal_key": deal_key},
            )

        return GDBSDeal(
            deal_id=deal_data["deal_id"],
            tenant_id=deal_data["tenant_id"],
            deal_key=deal_key,
            scenario=deal_data["scenario"],
            company_name=deal_data["company_name"],
            stage=deal_data.get("stage", ""),
            sector=deal_data.get("sector", ""),
            artifacts=artifacts_data.get("artifacts", []),
            spans=spans_data.get("spans", []),
            claims=claims_data.get("claims", []),
            evidence=evidence_data.get("evidence_items", []),
            sanads=sanads_data.get("sanads", []),
            defects=defects_data.get("defects", []),
            calcs=calcs_data.get("calc_sanads", []),
            raw=deal_data,
        )

    def _load_deals(self, manifest: dict[str, Any]) -> list[GDBSDeal]:
        """Load all deals from manifest."""
        deals = []
        deals_config = manifest.get("deals", [])
        if not deals_config:
            raise GDBSLoadError("No deals defined in manifest", self.dataset_path / "manifest.json")

        for deal_cfg in deals_config:
            deal_key = deal_cfg.get("deal_key")
            deal_dir_rel = deal_cfg.get("directory")
            if not deal_key or not deal_dir_rel:
                raise GDBSLoadError(
                    "Deal config missing deal_key or directory",
                    self.dataset_path / "manifest.json",
                    {"deal_cfg": deal_cfg},
                )
            deal_dir = self.dataset_path / deal_dir_rel
            deal = self._load_deal(deal_dir, deal_key)
            deals.append(deal)

        return deals

    def _load_expected_outcomes(self, manifest: dict[str, Any]) -> dict[str, GDBSExpectedOutcome]:
        """Load all expected outcomes based on manifest deals."""
        outcomes: dict[str, GDBSExpectedOutcome] = {}
        outcomes_dir = self.dataset_path / "expected_outcomes"
        if not outcomes_dir.exists():
            raise GDBSLoadError("Expected outcomes directory not found", outcomes_dir)

        # Load expected outcomes for all deals in manifest (supports 100 deals)
        deals_config = manifest.get("deals", [])
        for i, _deal_cfg in enumerate(deals_config, 1):
            filename = f"deal_{i:03d}_expected.json"
            path = outcomes_dir / filename
            if not path.exists():
                raise GDBSLoadError(f"Expected outcome file not found: {filename}", path)

            data = self._load_json(path)
            deal_key = data.get("deal_key", f"deal_{i:03d}")
            outcomes[deal_key] = GDBSExpectedOutcome(
                deal_id=data.get("deal_id", ""),
                deal_key=deal_key,
                scenario=data.get("scenario", ""),
                expected_claims=data.get("expected_claims", {}),
                expected_defects=data.get("expected_defects", []),
                validation_rules=data.get("validation_rules", []),
                raw=data,
            )

        return outcomes

    def _load_audit_expectations(self) -> dict[str, Any]:
        """Load audit expectations."""
        path = self.dataset_path / "audit_expectations" / "required_events.json"
        return self._load_json(path)

    def load(self) -> GDBSDataset:
        """
        Load complete GDBS dataset.

        Fail-closed: raises GDBSLoadError on any validation failure.
        """
        for required_file in self.REQUIRED_FILES:
            path = self.dataset_path / required_file
            if not path.exists():
                raise GDBSLoadError(f"Required file missing: {required_file}", path)

        manifest = self._load_json(self.dataset_path / "manifest.json")
        self._validate_required_fields(
            manifest,
            ["manifest_version", "dataset_id", "version", "deals"],
            self.dataset_path / "manifest.json",
        )

        tenant = self._load_tenant()
        actors = self._load_actors()
        deals = self._load_deals(manifest)
        expected_outcomes = self._load_expected_outcomes(manifest)
        audit_expectations = self._load_audit_expectations()

        return GDBSDataset(
            manifest=manifest,
            tenant=tenant,
            actors=actors,
            deals=deals,
            expected_outcomes=expected_outcomes,
            audit_expectations=audit_expectations,
            dataset_path=self.dataset_path,
        )

    def validate_tenant_isolation(self, dataset: GDBSDataset, wrong_tenant_id: str) -> bool:
        """
        Validate that accessing data with wrong tenant returns no data.

        Returns True if isolation is enforced (no data returned for wrong tenant).
        """
        for deal in dataset.deals:
            if deal.tenant_id == wrong_tenant_id:
                return False
            for claim in deal.claims:
                if claim.get("tenant_id") == wrong_tenant_id:
                    return False
            for sanad in deal.sanads:
                if sanad.get("tenant_id") == wrong_tenant_id:
                    return False
        return True
