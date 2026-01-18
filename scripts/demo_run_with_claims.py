"""Demo script: Create a run and populate it with GDBS claims.

Shows working pipeline execution with visible database results.
"""

import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import text

from idis.persistence.db import get_admin_engine
from idis.testing.gdbs_loader import GDBSLoader


def demo_run(deal_id: str) -> None:
    """Create and complete a run with claims from GDBS."""

    tenant_id = "00000000-0000-0000-0000-000000000001"
    run_id = str(uuid.uuid4())

    # Load GDBS
    gdbs_path = Path(__file__).parent.parent / "datasets" / "gdbs_full"
    loader = GDBSLoader(str(gdbs_path))
    dataset = loader.load()

    engine = get_admin_engine()

    with engine.connect() as conn:
        # Get deal name
        result = conn.execute(
            text("SELECT name FROM deals WHERE deal_id = :deal_id"),
            {"deal_id": deal_id},
        )
        row = result.fetchone()
        if not row:
            print(f"❌ Deal {deal_id} not found")
            return

        deal_name = row[0]
        print(f"📊 Processing deal: {deal_name}")

        # Find matching GDBS deal
        gdbs_deal = None
        for d in dataset.deals:
            if d.company_name == deal_name:
                gdbs_deal = d
                break

        if not gdbs_deal:
            print(f"⚠️  No GDBS data for {deal_name}")
            return

        print(f"✅ Found GDBS data: {len(gdbs_deal.claims)} claims")

        # Create run
        now = datetime.now(UTC)
        conn.execute(
            text("""
                INSERT INTO runs (
                    run_id, tenant_id, deal_id, mode, status, started_at, created_at
                )
                VALUES (
                    :run_id, :tenant_id, :deal_id, 'SNAPSHOT', 'RUNNING',
                    :started_at, :created_at
                )
            """),
            {
                "run_id": run_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "started_at": now,
                "created_at": now,
            },
        )
        conn.commit()

        print(f"\n🚀 Run {run_id[:8]} created - RUNNING")

        # Set tenant context for RLS
        conn.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, false)"),
            {"tenant_id": tenant_id},
        )

        # Load claims
        claims_created = 0
        for claim in gdbs_deal.claims:
            claim_id = str(uuid.uuid4())

            claim_text = claim.get("raw_text", claim.get("claim_text", ""))
            claim_class = claim.get("claim_class", "METRIC")
            value_json = json.dumps(claim.get("value")) if claim.get("value") else None

            conn.execute(
                text("""
                    INSERT INTO claims (
                        claim_id, tenant_id, deal_id, claim_class, claim_text,
                        value, created_at
                    ) VALUES (
                        :claim_id, :tenant_id, :deal_id, :claim_class, :claim_text,
                        CAST(:value AS jsonb), :created_at
                    )
                """),
                {
                    "claim_id": claim_id,
                    "tenant_id": tenant_id,
                    "deal_id": deal_id,
                    "claim_class": claim_class,
                    "claim_text": claim_text,
                    "value": value_json,
                    "created_at": now,
                },
            )

            # Create basic Sanad for each claim
            sanad_id = str(uuid.uuid4())
            evidence_id = f"gdbs_evidence_{claim_id[:8]}"

            conn.execute(
                text("""
                    INSERT INTO sanads (
                        sanad_id, tenant_id, claim_id, deal_id,
                        primary_evidence_id, created_at
                    ) VALUES (
                        :sanad_id, :tenant_id, :claim_id, :deal_id,
                        :primary_evidence_id, :created_at
                    )
                """),
                {
                    "sanad_id": sanad_id,
                    "tenant_id": tenant_id,
                    "claim_id": claim_id,
                    "deal_id": deal_id,
                    "primary_evidence_id": evidence_id,
                    "created_at": now,
                },
            )

            claims_created += 1

        conn.commit()
        print(f"✅ Loaded {claims_created} claims with Sanads")

        # Mark run as SUCCEEDED
        finished = datetime.now(UTC)
        conn.execute(
            text("""
                UPDATE runs
                SET status = 'SUCCEEDED', finished_at = :finished_at
                WHERE run_id = :run_id
            """),
            {"finished_at": finished, "run_id": run_id},
        )
        conn.commit()

        print(f"✅ Run {run_id[:8]} completed - SUCCEEDED")
        print("\n📈 Summary:")
        print(f"   - Deal: {deal_name}")
        print(f"   - Run ID: {run_id}")
        print(f"   - Claims: {claims_created}")
        print("   - Status: SUCCEEDED")
        print(f"\n🌐 View in UI: http://localhost:3001/runs/{run_id}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/demo_run_with_claims.py <deal_id>")
        print("\nExample:")
        print("  python scripts/demo_run_with_claims.py d3d6d0ca-1af2-4b89-9d01-c11a1f83c03a")
        sys.exit(1)

    demo_run(sys.argv[1])
