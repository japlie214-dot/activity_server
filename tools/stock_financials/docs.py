# tools/stock_financials/docs.py
TOOL_DESCRIPTION = "Extract and query quarterly financial statements (Income, Balance Sheet, Cash Flow) from SEC EDGAR filings via XBRL data."

TOOL_DOCS = """## Stock Financials

Extract, normalize, and query quarterly financial facts from SEC EDGAR filings.
Supports Income Statement, Balance Sheet, and Cash Flow Statement.

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
| 3 | stock_financials.format | Render results as markdown |

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
| `refresh` | boolean | false | Force re-extraction from EDGAR |

#### Query Instructions

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ticker` | string | required | Stock ticker |
| `statement_type` | string | "income" | One of: income, balance, cashflow |
| `concept` | string | null | XBRL concept (e.g., "us-gaap:Revenue") |
| `start_quarter` | string | null | Start quarter (e.g., "2024-Q1") |
| `end_quarter` | string | null | End quarter (e.g., "2026-Q1") |
| `limit` | integer | 100 | Max results (1-500) |

### Common Workflows

1. **Extract then query:**
   ```json
   {"command": "extract", "instructions": {"ticker": "NVDA", "quarters": 8}}
   {"command": "query", "instructions": {"ticker": "NVDA", "statement_type": "income"}}
   ```

2. **Query specific concept:**
   ```json
   {"command": "query", "instructions": {"ticker": "AAPL", "concept": "us-gaap:Revenue"}}
   ```

3. **Check cache status:**
   ```json
   {"command": "status", "instructions": {"ticker": "MSFT"}}
   ```

### Troubleshooting

- **Missing Q4 data:** Request more quarters to include FY data.
- **Stale data:** Use `refresh: true` to force re-extraction.
- **No results:** Run `extract` first to populate the cache.
"""

TOOL_OUTPUT_EXAMPLE = {
    "ticker": "AAPL",
    "company": "APPLE INC",
    "quarters": 8,
    "rows": 240,
    "cache_hit": False,
    "markdown": "**AAPL** (APPLE INC) — Extracted 8 quarters.\n..."
}
