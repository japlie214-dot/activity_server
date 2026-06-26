# tools/stock_financials/docs.py
TOOL_DESCRIPTION = (
    "Extract and query quarterly financial statements "
    "(Income, Balance Sheet, Cash Flow) from SEC EDGAR filings via XBRL data."
)

TOOL_DOCS = """## Stock Financials

Extract, normalize, and query quarterly financial facts from SEC EDGAR filings.
Supports Income Statement, Balance Sheet, and Cash Flow Statement.

All data is synced to cloud backup automatically.

### Commands

| Command | Description |
|---------|-------------|
| `extract` | Fetch quarters from SEC EDGAR for a given ticker |
| `query` | Query cached financial facts from the operational database |
| `status` | Check what quarters are currently cached |
| `catalog` | List available XBRL concepts for a ticker |

### Activities

| # | Activity | Purpose |
|---|----------|---------|
| 1 | stock_financials.validate | Parse and validate command + instructions |
| 2 | stock_financials.execute | Run the appropriate sub-command |
| 3 | stock_financials.format | Normalize result into response dict |

### Input Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `command` | string | ✅ | One of: extract, query, status, catalog |
| `instructions` | object | ✅ | Command-specific parameters |

#### Extract Instructions

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ticker` | string | required | Stock ticker (e.g., "AAPL") |
| `quarters` | integer | 8 | Number of quarters to extract (max 40) |
| `refresh` | boolean | false | Force re-extraction from EDGAR (purges local + cloud) |

#### Query Instructions

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ticker` | string | required | Stock ticker |
| `statement_type` | string | "income" | One of: **income**, **balance**, **cashflow** |
| `concept` | string | null | XBRL concept (e.g., "us-gaap:Revenue") |
| `start_quarter` | string | null | Start quarter (e.g., "2024-Q1") |
| `end_quarter` | string | null | End quarter (e.g., "2026-Q1") |
| `limit` | integer | 100 | Max results (1-500) |

### Statement Types

The `query` command supports three statement types via the `statement_type` field:

- **`income`** — Income Statement (Revenue, Gross Profit, Operating Income, Net Income, EPS)
- **`balance`** — Balance Sheet (Assets, Liabilities, Equity, Cash)
- **`cashflow`** — Cash Flow Statement (Operating CF, Investing CF, Financing CF)

### Common Workflows

1. **Extract then query income:**
   ```json
   {"command": "extract", "instructions": {"ticker": "NVDA", "quarters": 8}}
   {"command": "query", "instructions": {"ticker": "NVDA", "statement_type": "income"}}
   ```

2. **Query balance sheet:**
   ```json
   {"command": "query", "instructions": {"ticker": "AAPL", "statement_type": "balance"}}
   ```

3. **Query specific concept across cashflow:**
   ```json
   {"command": "query", "instructions": {"ticker": "AAPL", "statement_type": "cashflow", "concept": "us-gaap:NetCashProvidedByUsedInOperatingActivities"}}
   ```

4. **Check cache status:**
   ```json
   {"command": "status", "instructions": {"ticker": "MSFT"}}
   ```

### Troubleshooting

- **Missing Q4 data:** Request more quarters to include FY data.
- **Stale data:** Use `refresh: true` to force re-extraction (purges local + cloud).
- **No results:** Run `extract` first to populate the cache.
"""

TOOL_OUTPUT_EXAMPLE = {
    "ticker": "AAPL",
    "company": "APPLE INC",
    "quarters_requested": 8,
    "quarters_cached": 8,
    "cache_hit": False,
    "refresh": False,
    "total_rows": 240,
    "coverage": {
        "income": {
            "label": "Income Statement",
            "rows": 80,
            "quarter_count": 8,
            "latest_quarter": "2026-Q2",
        },
        "balance": {
            "label": "Balance Sheet",
            "rows": 80,
            "quarter_count": 8,
            "latest_quarter": "2026-Q2",
        },
        "cashflow": {
            "label": "Cash Flow Statement",
            "rows": 80,
            "quarter_count": 8,
            "latest_quarter": "2026-Q2",
        },
    },
    "key_metrics": {
        "income": {
            "label": "Income Statement",
            "quarters_shown": ["2026-Q2", "2026-Q1", "2025-Q4", "2025-Q3"],
            "metrics": {
                "us-gaap:Revenues": {
                    "label": "Revenue",
                    "unit": "USD",
                    "quarters": {
                        "2026-Q2": {"raw": 95000000000, "formatted": "$95.0B"},
                    },
                },
            },
        },
    },
}
