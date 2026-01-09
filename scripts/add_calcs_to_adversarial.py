#!/usr/bin/env python3
"""Add calcs.json to adversarial deals 1-5, 7-8 that are missing them."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

SEED = 20260109
TENANT_ID = "00000000-0000-0000-0000-000000000001"
BASE_DATE = "2026-01-05"


class DeterministicGenerator:
    """Seeded deterministic value generator."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)

    def arr(self, deal_num: int) -> int:
        self.rng.seed(SEED + deal_num + 3000)
        base = self.rng.randint(1000, 20000)
        return base * 1000

    def gross_margin(self, deal_num: int) -> float:
        self.rng.seed(SEED + deal_num + 4000)
        return round(self.rng.uniform(55.0, 85.0), 2)

    def burn(self, deal_num: int) -> int:
        self.rng.seed(SEED + deal_num + 5000)
        base = self.rng.randint(1000, 8000)
        return base * 100

    def cash(self, deal_num: int) -> int:
        self.rng.seed(SEED + deal_num + 6000)
        return self.rng.randint(5, 30) * 1000000


def generate_calc_id(deal_num: int, calc_num: int) -> str:
    return f"00000000-0000-0000-0008-{deal_num:06d}{calc_num:06d}"


def generate_claim_id(deal_num: int, claim_num: int) -> str:
    return f"00000000-0000-0000-0005-{deal_num:06d}{claim_num:06d}"


def generate_deal_id(deal_num: int) -> str:
    return f"00000000-0000-0000-0002-{deal_num:012d}"


def create_calcs_for_deal(deal_num: int, gen: DeterministicGenerator) -> dict:
    """Create calcs.json content for a deal."""
    arr = gen.arr(deal_num)
    gm = gen.gross_margin(deal_num)
    burn = gen.burn(deal_num)
    cash = gen.cash(deal_num)

    revenue = arr
    cogs = int(revenue * (1 - gm / 100))
    calc_gm = round((revenue - cogs) / revenue * 100, 2) if revenue > 0 else 0.0
    calc_runway = round(cash / burn, 1) if burn > 0 else 0.0

    deal_id = generate_deal_id(deal_num)

    return {
        "calc_sanads": [
            {
                "calc_sanad_id": generate_calc_id(deal_num, 1),
                "tenant_id": TENANT_ID,
                "deal_id": deal_id,
                "calc_id": f"calc-gm-{deal_num:03d}",
                "calc_type": "GROSS_MARGIN",
                "input_claim_ids": [generate_claim_id(deal_num, 2)],
                "input_min_sanad_grade": "C",
                "inputs": {"revenue": revenue, "cogs": cogs},
                "formula_hash": "sha256:gm_v1_rev_minus_cogs_div_rev",
                "code_version": "idis-calc-service@1.0.0",
                "output": {"gross_margin_percent": calc_gm},
                "reproducibility_hash": hashlib.sha256(
                    f"gm:{revenue}:{cogs}:{calc_gm}".encode()
                ).hexdigest()[:16],
                "calc_grade": "B",
                "explanation": (
                    f"GM = (Rev - COGS) / Rev = ({revenue} - {cogs}) / {revenue} = {calc_gm}%"
                ),
                "created_at": f"{BASE_DATE}T11:00:00Z",
                "updated_at": f"{BASE_DATE}T11:00:00Z",
            },
            {
                "calc_sanad_id": generate_calc_id(deal_num, 2),
                "tenant_id": TENANT_ID,
                "deal_id": deal_id,
                "calc_id": f"calc-runway-{deal_num:03d}",
                "calc_type": "RUNWAY",
                "input_claim_ids": [generate_claim_id(deal_num, 3), generate_claim_id(deal_num, 4)],
                "input_min_sanad_grade": "C",
                "inputs": {"cash_balance": cash, "monthly_burn": burn},
                "formula_hash": "sha256:runway_v1_cash_div_burn",
                "code_version": "idis-calc-service@1.0.0",
                "output": {"runway_months": calc_runway},
                "reproducibility_hash": hashlib.sha256(
                    f"runway:{cash}:{burn}:{calc_runway}".encode()
                ).hexdigest()[:16],
                "calc_grade": "B",
                "explanation": f"Runway = Cash / Burn = {cash} / {burn} = {calc_runway} months",
                "created_at": f"{BASE_DATE}T11:00:00Z",
                "updated_at": f"{BASE_DATE}T11:00:00Z",
            },
        ]
    }


def main() -> None:
    repo_root = Path(__file__).parent.parent
    deals_dir = repo_root / "datasets" / "gdbs_full" / "deals"

    gen = DeterministicGenerator(SEED)

    # Deals that need calcs.json (1-5, 7-8; deal 6 already has it)
    deal_dirs = [
        (1, "deal_001_clean"),
        (2, "deal_002_contradiction"),
        (3, "deal_003_unit_mismatch"),
        (4, "deal_004_time_window_mismatch"),
        (5, "deal_005_missing_evidence"),
        (7, "deal_007_chain_break"),
        (8, "deal_008_version_drift"),
    ]

    for deal_num, deal_dir_name in deal_dirs:
        deal_dir = deals_dir / deal_dir_name
        calcs_path = deal_dir / "calcs.json"

        if calcs_path.exists():
            print(f"Skipping {deal_dir_name} - calcs.json already exists")
            continue

        calcs = create_calcs_for_deal(deal_num, gen)
        calcs_path.write_text(json.dumps(calcs, indent=2), encoding="utf-8")
        print(f"Created calcs.json for {deal_dir_name}")


if __name__ == "__main__":
    main()
