"""Single-deal smoke test with real Anthropic LLM through full 9-step pipeline."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import httpx
from idis.storage.filesystem_store import FilesystemObjectStore

BASE = "http://127.0.0.1:8777"
API_KEY = "gate3-harness-key"
TENANT = "00000000-0000-0000-0000-000000000001"
STORE_DIR = Path(os.environ["TEMP"]) / "gate3_store_smoke"
DEALS_DIR = Path("datasets/gdbs_full/deals")

headers = {"X-IDIS-API-Key": API_KEY}
fs = FilesystemObjectStore(base_dir=STORE_DIR)

# Pick first deal only
deal_dir = sorted(DEALS_DIR.iterdir())[0]
deal_name = deal_dir.name

print(f"\n{'='*60}", flush=True)
print(f"SMOKE TEST — {deal_name}", flush=True)
print(f"{'='*60}", flush=True)

# 1. Create deal
print(f"  [1] Creating deal...", flush=True)
r = httpx.post(
    f"{BASE}/v1/deals",
    json={"name": deal_name, "company_name": f"SmokeTest-{deal_name}"},
    headers=headers,
    timeout=30,
)
print(f"      -> {r.status_code}", flush=True)
if r.status_code != 201:
    print(f"FAIL: Deal creation returned {r.status_code}: {r.text}", flush=True)
    sys.exit(1)
deal_id = r.json()["deal_id"]
print(f"      deal_id={deal_id}", flush=True)

# 2. Seed + ingest documents
artifacts_json = deal_dir / "artifacts.json"
if not artifacts_json.exists():
    print("FAIL: No artifacts.json", flush=True)
    sys.exit(1)

manifest = json.loads(artifacts_json.read_text())
for art in manifest["artifacts"]:
    fn = art["filename"]
    fp = deal_dir / "artifacts" / fn
    if not fp.exists():
        fp = deal_dir / fn
    if not fp.exists():
        print(f"      SKIP {fn} (not found)", flush=True)
        continue
    raw = fp.read_bytes()
    key = f"smoke/{deal_name}/{fn}"
    fs.put(tenant_id=TENANT, key=key, data=raw)
    if "pitch" in fn:
        doc_type = "PITCH_DECK"
    elif "financ" in fn:
        doc_type = "FINANCIAL_MODEL"
    else:
        doc_type = "DATA_ROOM_FILE"
    print(f"  [2] Ingesting {fn} ({len(raw)} bytes)...", flush=True)
    r2 = httpx.post(
        f"{BASE}/v1/deals/{deal_id}/documents",
        json={"doc_type": doc_type, "title": fn, "uri": f"file://{key}", "auto_ingest": True},
        headers={**headers, "Content-Type": "application/json"},
        timeout=30,
    )
    print(f"      -> {r2.status_code}", flush=True)

# 3. Fire pipeline
print(f"  [3] Starting FULL pipeline...", flush=True)
print(f"      Start: {time.strftime('%H:%M:%S')}", flush=True)
t0 = time.time()
try:
    r3 = httpx.post(
        f"{BASE}/v1/deals/{deal_id}/runs",
        json={"mode": "FULL"},
        headers={**headers, "Idempotency-Key": f"smoke-{deal_name}-v4"},
        timeout=3600,
    )
    elapsed = time.time() - t0
    print(f"      End:   {time.strftime('%H:%M:%S')}", flush=True)
    print(f"      -> HTTP {r3.status_code} in {elapsed:.1f}s", flush=True)
    body = r3.json()
    body_str = json.dumps(body, indent=2, default=str)
    if len(body_str) > 5000:
        body_str = body_str[:5000] + "\n... (truncated)"
    print(body_str, flush=True)

    # Check step results
    if isinstance(body, dict):
        steps = body.get("steps", [])
        if steps:
            print(f"\n{'='*60}", flush=True)
            print("STEP RESULTS:", flush=True)
            for s in steps:
                name = s.get("step_name", "?")
                status = s.get("status", "?")
                err = s.get("error_message", "")
                mark = "PASS" if status == "COMPLETED" else "FAIL"
                suffix = f" — {err}" if err else ""
                print(f"  {name}: {mark}{suffix}", flush=True)
            print(f"{'='*60}", flush=True)

        completed_count = sum(1 for s in steps if s.get("status") == "COMPLETED")
        total = len(steps)
        if completed_count == total and total == 9:
            print(f"\nSMOKE TEST PASSED — {completed_count}/{total} steps in {elapsed:.1f}s", flush=True)
        else:
            print(f"\nSMOKE TEST FAILED — {completed_count}/{total} steps completed in {elapsed:.1f}s", flush=True)
    else:
        print(f"\nSMOKE TEST FAILED — unexpected response type: {type(body).__name__}", flush=True)
except Exception as exc:
    elapsed = time.time() - t0
    print(f"      End:   {time.strftime('%H:%M:%S')}", flush=True)
    print(f"      -> EXCEPTION in {elapsed:.1f}s: {exc}", flush=True)
    print(f"\nSMOKE TEST FAILED — exception", flush=True)
    sys.exit(1)
