#!/usr/bin/env python3
"""Test stock_financials extraction for 20 tickers via REST API."""
import json
import time
import httpx

BASE = "http://localhost:8081"
TICKERS = [
    "BABA", "TSM",
]

results = {}
client = httpx.Client(timeout=300)

for i, ticker in enumerate(TICKERS):
    print(f"\n[{i+1}/{len(TICKERS)}] Extracting {ticker}...", flush=True)
    t0 = time.time()
    try:
        resp = client.post(f"{BASE}/tools/stock_financials", json={
            "arguments": {
                "command": "extract",
                "instructions": {"ticker": ticker, "quarters": 4, "refresh": True}
            }
        })
        elapsed = time.time() - t0
        data = resp.json()
        result = data.get("result", data)

        if "error" in result:
            print(f"  ❌ ERROR: {result['error']} ({elapsed:.1f}s)")
            results[ticker] = {"status": "error", "error": result["error"], "time": elapsed}
            continue

        persisted = result.get("persisted", {})
        total = result.get("total_rows", 0)
        company = result.get("company", "?")
        cache_hit = result.get("cache_hit", False)
        quarters = result.get("quarters_cached", 0)

        print(f"  ✅ {company} | rows={total} | quarters={quarters} | "
              f"income={persisted.get('income',0)} balance={persisted.get('balance',0)} "
              f"cashflow={persisted.get('cashflow',0)} | "
              f"{'cache_hit' if cache_hit else 'extracted'} | {elapsed:.1f}s")
        results[ticker] = {
            "status": "ok",
            "company": company,
            "total_rows": total,
            "quarters": quarters,
            "persisted": persisted,
            "cache_hit": cache_hit,
            "time": elapsed,
        }
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  ❌ EXCEPTION: {e} ({elapsed:.1f}s)")
        results[ticker] = {"status": "exception", "error": str(e), "time": elapsed}

# Summary
print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
ok = sum(1 for r in results.values() if r["status"] == "ok")
fail = sum(1 for r in results.values() if r["status"] != "ok")
print(f"Passed: {ok}/{len(TICKERS)} | Failed: {fail}/{len(TICKERS)}")
for t, r in results.items():
    status = "✅" if r["status"] == "ok" else "❌"
    extra = ""
    if r["status"] == "ok":
        extra = f"rows={r['total_rows']} q={r['quarters']} {r['time']:.1f}s"
    else:
        extra = r.get("error", str(r))[:60]
    print(f"  {status} {t:6s} {extra}")

# Check sync status
print("\nChecking sync...")
try:
    resp = client.get(f"{BASE}/sync")
    sync_data = resp.json()
    tables = sync_data.get("tables", {})
    for tbl, info in tables.items():
        if not info.get("match", True):
            print(f"  ⚠ {tbl}: op={info['op_count']} cloud={info['cloud_count']} mismatches={info['hash_mismatches']}")
    if all(info.get("match", True) for info in tables.values()):
        print("  ✓ All tables in sync")
except Exception as e:
    print(f"  Sync check failed: {e}")

client.close()
