# tools/stock_notes/docs.py
TOOL_DESCRIPTION = "Extract and explore SEC EDGAR filing footnotes (10-K, 10-Q, 20-F, 6-K) — narratives, embedded data tables, and dimensional XBRL concepts."

TOOL_DOCS = """## Stock Notes

An advanced footnote extraction and analysis tool that drills into detailed SEC
filing footnotes to isolate narratives, parse embedded data tables, and expose
dimensional XBRL concepts in tidy format.

### Commands

| Command | Description |
|---------|-------------|
| `discover` | Find and list recent filings for a ticker |
| `note` | List all notes in a filing, or drill into a specific note |
| `details` | Extract time-series data for a specific XBRL concept |

### Activities

| # | Activity | Purpose |
|---|----------|---------|
| 1 | stock_notes.validate | Parse and validate command + instructions |
| 2 | stock_notes.execute | Run the appropriate sub-command |
| 3 | stock_notes.format | Render results as markdown |

### Input Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `command` | string | ✅ | One of: discover, note, details |
| `instructions` | object | ✅ | Command-specific parameters |

#### Discover Instructions

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ticker` | string | required | Stock ticker (e.g., "AAPL") |
| `forms` | string | "10-K,10-Q" | Comma-separated form types |

#### Note Instructions

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `accession_no` | string | required | SEC accession number |
| `note_number` | integer | null | Specific note to drill into |
| `ticker` | string | "" | Ticker (auto-detected if empty) |
| `force_refresh` | boolean | false | Force re-extraction from EDGAR |

#### Details Instructions

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ticker` | string | required | Stock ticker |
| `concept` | string | required | XBRL concept (e.g., "us-gaap:LongTermDebt") |
| `start_date` | string | null | Start date in YYYY-MM format |
| `end_date` | string | null | End date in YYYY-MM format |

### Common Workflows

1. **Discover filings, then explore notes:**
   ```json
   {"command": "discover", "instructions": {"ticker": "AAPL"}}
   {"command": "note", "instructions": {"accession_no": "0000320193-26-000013"}}
   ```

2. **Drill into a specific note:**
   ```json
   {"command": "note", "instructions": {"accession_no": "0000320193-26-000013", "note_number": 6}}
   ```

3. **Query concept time-series:**
   ```json
   {"command": "details", "instructions": {"ticker": "AAPL", "concept": "us-gaap:LongTermDebt", "start_date": "2024-09", "end_date": "2026-03"}}
   ```

### Troubleshooting

- **No data found:** Run `note` with the accession number first to hydrate the cache.
- **Stale data:** Use `force_refresh: true` to re-extract from EDGAR.
- **Missing concepts:** The footnote may not have been hydrated yet.
"""

TOOL_OUTPUT_EXAMPLE = {
    "command": "discover",
    "ticker": "AAPL",
    "filings_count": 20,
    "markdown": "# Filings for AAPL (20 found)\n| # | Form | Filing Date | ..."
}
