"""
Load GDBS-FULL synthetic deals into IDIS via API for demo/testing.

Usage:
    python scripts/load_gdbs_deals.py [--count N] [--api-key KEY]
"""

import argparse
import sys
from pathlib import Path

import requests

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from idis.testing.gdbs_loader import GDBSLoader


def load_deals(count: int, api_key: str, base_url: str) -> None:
    """Load GDBS deals via API."""

    # Load GDBS dataset
    dataset_path = Path(__file__).parent.parent / "datasets" / "gdbs_full"
    print(f"Loading GDBS dataset from {dataset_path}")

    loader = GDBSLoader(str(dataset_path))
    dataset = loader.load()

    print(f"Loaded {len(dataset.deals)} deals from GDBS")
    print(f"Will create first {min(count, len(dataset.deals))} deals via API")
    print(f"API: {base_url}")
    print()

    headers = {
        "X-IDIS-API-Key": api_key,
        "Content-Type": "application/json",
    }

    created_count = 0

    # Insert deals
    for i, deal in enumerate(dataset.deals[:count]):
        print(f"[{i + 1}/{count}] Creating {deal.deal_key} ({deal.scenario})...", end=" ")

        try:
            response = requests.post(
                f"{base_url}/v1/deals",
                headers=headers,
                json={
                    "name": deal.company_name,
                    "company_name": deal.company_name,
                    "stage": deal.stage,
                    "tags": [deal.sector, deal.scenario],
                },
                timeout=10,
            )

            if response.status_code == 201:
                created_deal = response.json()
                print(f"✓ {created_deal['deal_id'][:8]}")
                created_count += 1
            else:
                print(f"✗ HTTP {response.status_code}: {response.text[:100]}")

        except Exception as e:
            print(f"✗ Error: {e}")
            continue

    print()
    print(f"✅ Created {created_count}/{count} deals successfully")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load GDBS deals into IDIS via API")
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="Number of deals to load (default: 10)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default="test-key-123",
        help="API key for authentication (default: test-key-123)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="http://localhost:8000",
        help="Base URL for API (default: http://localhost:8000)",
    )

    args = parser.parse_args()

    load_deals(args.count, args.api_key, args.base_url)


if __name__ == "__main__":
    main()
