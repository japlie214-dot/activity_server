#!/usr/bin/env python3
"""Validate stock_financials data against yfinance for accuracy."""
import httpx
import yfinance as yf
import pandas as pd

BASE = "http://localhost:8081"
TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "JPM",
           "V", "JNJ", "PFE", "KO", "WMT", "XOM", "BAC", "HD", "UNH", "PG"]

# (our_concept, yfinance_field, statement_type)
VALIDATE = [
    # Income
    ("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "Total Revenue", "income"),
    ("us-gaap:Revenues", "Total Revenue", "income"),
    ("us-gaap:SalesRevenueNet", "Total Revenue", "income"),
    ("us-gaap:NetIncomeLoss", "Net Income", "income"),
    ("us-gaap:GrossProfit", "Gross Profit", "income"),
    ("us-gaap:OperatingIncomeLoss", "Operating Income", "income"),
    # Balance
    ("us-gaap:Assets", "Total Assets", "balance"),
    ("us-gaap:Liabilities", "Total Liabilities Net Minority Interest", "balance"),
    ("us-gaap:StockholdersEquity", "Stockholders Equity", "balance"),
    ("us-gaap:CashAndCashEquivalentsAtCarryingValue", "Cash And Cash Equivalents", "balance"),
    # Cashflow
    ("us-gaap:NetCashProvidedByUsedInOperatingActivities", "Operating Cash Flow", "cashflow"),
    ("us-gaap:NetCashProvidedByUsedInInvestingActivities", "Investing Cash Flow", "cashflow"),
    ("us-gaap:NetCashProvidedByUsedInFinancingActivities", "Financing Cash Flow", "cashflow"),
]

client = httpx.Client(timeout=30)
all_results = []

for ticker in TICKERS:
    print(f"\n{'='*60}")
    print(f"Validating {ticker}")
    print(f"{'='*60}")

    # Fetch our data
    our_data = {}
    for stype in ["income", "balance", "cashflow"]:
        resp = client.post(f"{BASE}/tools/stock_financials", json={
            "arguments": {
                "command": "query",
                "instructions": {"ticker": ticker, "statement_type": stype, "limit": 500}
            }
        })
        result = resp.json().get("result", {})
        our_data[stype] = result.get("data", {})

    # Fetch yfinance data
    try:
        yf_ticker = yf.Ticker(ticker)
        yf_q_fin = yf_ticker.quarterly_financials
        yf_q_bs = yf_ticker.quarterly_balance_sheet
        yf_q_cf = yf_ticker.quarterly_cashflow
    except Exception as e:
        print(f"  ❌ yfinance error: {e}")
        all_results.append({"ticker": ticker, "status": "error", "error": str(e)})
        continue

    yf_map = {"income": yf_q_fin, "balance": yf_q_bs, "cashflow": yf_q_cf}

    matches = 0
    mismatches = 0
    skipped = 0
    details = []

    for our_concept, yf_field, stype in VALIDATE:
        our_concept_data = our_data.get(stype, {}).get(our_concept)
        if not our_concept_data:
            skipped += 1
            continue

        # Get our latest quarter value
        quarter_keys = [k for k in our_concept_data.keys() if k not in ("label", "concept")]
        if not quarter_keys:
            skipped += 1
            continue
        latest_q = sorted(quarter_keys, reverse=True)[0]
        our_val = our_concept_data[latest_q].get("raw")
        if our_val is None:
            skipped += 1
            continue

        # Get yfinance value (latest quarter)
        yf_df = yf_map.get(stype)
        if yf_df is None or yf_df.empty:
            skipped += 1
            continue
        if yf_field not in yf_df.index:
            skipped += 1
            continue
        yf_val = yf_df.loc[yf_field, yf_df.columns[0]]
        if pd.isna(yf_val):
            skipped += 1
            continue

        # Compare (5% tolerance)
        try:
            our_f = float(our_val)
            yf_f = float(yf_val)
            if yf_f == 0:
                pct_diff = 0 if our_f == 0 else 100
            else:
                pct_diff = abs(our_f - yf_f) / abs(yf_f) * 100

            label = our_concept_data.get("label", our_concept.split(":")[-1])
            if pct_diff < 5:
                matches += 1
                details.append(f"  ✅ {label}: ours={our_f:,.0f} yf={yf_f:,.0f} ({pct_diff:.1f}%)")
            else:
                mismatches += 1
                details.append(f"  ⚠ {label}: ours={our_f:,.0f} yf={yf_f:,.0f} ({pct_diff:.1f}%)")
        except (ValueError, TypeError):
            skipped += 1

    for d in details:
        print(d)
    total = matches + mismatches
    status = "✅" if mismatches == 0 else "⚠"
    print(f"  {status} {matches}/{total} matched, {mismatches} mismatches, {skipped} skipped")
    all_results.append({"ticker": ticker, "matches": matches, "mismatches": mismatches, "skipped": skipped})

client.close()

# Summary
print(f"\n{'='*60}")
print("VALIDATION SUMMARY")
print(f"{'='*60}")
total_m = sum(r.get("matches", 0) for r in all_results)
total_mm = sum(r.get("mismatches", 0) for r in all_results)
total_s = sum(r.get("skipped", 0) for r in all_results)
print(f"Total: {total_m} matches, {total_mm} mismatches, {total_s} skipped\n")
for r in all_results:
    if "error" in r:
        print(f"  ❌ {r['ticker']}: {r['error']}")
    else:
        s = "✅" if r.get("mismatches", 0) == 0 else "⚠"
        t = r.get("matches", 0) + r.get("mismatches", 0)
        print(f"  {s} {r['ticker']}: {r.get('matches',0)}/{t} match")
