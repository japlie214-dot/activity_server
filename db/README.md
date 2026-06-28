<!-- db/README.md -->
# Database Layer

## Schema Design — Justification

### Cloud-Synced Tables (`CLOUD_SYNC_TABLES`)

Tables with irreplaceable user-created content are synced to Snowflake.
Each synced table has a `row_hash` column for content-addressable sync verification.

#### `artifacts` — File artifacts produced by tools

**Why synced:** Files are the actual user-created work product. Losing them
means losing work. Cloud backup ensures durability.

| Column | Type | Why |
|--------|------|-----|
| `id` | INTEGER PRIMARY KEY | Row identifier, used for hash comparison across databases |
| `tool_name` | TEXT | Which tool created this artifact |
| `filename` | TEXT | Human-readable name |
| `filepath` | TEXT | Absolute path on disk |
| `size_bytes` | INTEGER | File size without reading the file |
| `content_preview` | TEXT | First 200 chars of content |
| `created_at` | TEXT | ISO-8601 timestamp |
| `row_hash` | TEXT | SHA-256 hash for sync verification |

#### `sf_tickers` — Stock ticker registry (stock_financials)

**Why synced:** Links tickers to CIK/company names. Irreplaceable metadata.

| Column | Type | Why |
|--------|------|-----|
| `ticker` | TEXT PRIMARY KEY | Stock ticker (e.g., "AAPL") |
| `company_name` | TEXT | Company name from EDGAR |
| `cik` | TEXT | SEC CIK number |
| `created_at` | TEXT | First extraction timestamp |
| `updated_at` | TEXT | Last update timestamp |

#### `sf_quarterly_facts` — Quarterly financial facts (stock_financials)

**Why synced:** Core financial data extracted from SEC EDGAR XBRL. Expensive
to re-fetch and irreplaceable once filings are amended or removed.

| Column | Type | Why |
|--------|------|-----|
| `id` | INTEGER PRIMARY KEY | Row identifier |
| `ticker` | TEXT | Stock ticker |
| `statement_type` | TEXT | income, balance, or cashflow |
| `concept` | TEXT | XBRL concept (e.g., "us-gaap:Revenues") |
| `label` | TEXT | Human-readable label from XBRL blueprint |
| `quarter` | TEXT | Quarter label (e.g., "2025-Q2") |
| `period_end` | TEXT | Period end date |
| `fiscal_period` | TEXT | Q1, Q2, Q3, Q4, or FY |
| `fiscal_year` | INTEGER | Fiscal year |
| `numeric_value` | REAL | The fact value |
| `unit` | TEXT | Unit of measure (USD, shares, etc.) |
| `period_type` | TEXT | duration or instant |
| `depth` | INTEGER | Nesting depth in statement blueprint |
| `is_total` | INTEGER | 1 if this is a total/subtotal line |
| `concept_order` | INTEGER | Ordering from the statement blueprint |
| `content_hash` | TEXT | MD5 hash for idempotent upserts |
| `extracted_at` | TEXT | When the data was fetched from EDGAR |
| `created_at` | TEXT | First insert timestamp |
| `updated_at` | TEXT | Last update timestamp |
| `row_hash` | TEXT | SHA-256 hash for cloud sync verification |

#### `sn_filings` — Filing metadata (stock_notes)

**Why synced:** Filing metadata (accession numbers, periods, quarters) is
the index into all footnote data. Losing it means losing the map.

| Column | Type | Why |
|--------|------|-----|
| `filing_id` | TEXT PRIMARY KEY | Composite: ticker|form|accession_no |
| `ticker` | TEXT | Stock ticker |
| `form` | TEXT | Form type (10-K, 10-Q, 20-F, 6-K) |
| `filing_date` | TEXT | Date filing was submitted |
| `accession_no` | TEXT | SEC accession number |
| `period_of_report` | TEXT | Reporting period end date |
| `company_name` | TEXT | Company name |
| `cik` | TEXT | SEC CIK number |
| `fiscal_year_end_month` | INTEGER | Fiscal year-end month (1-12) |
| `quarter` | INTEGER | Fiscal quarter (1-4) |
| `year` | INTEGER | Fiscal year |
| `content_hash` | TEXT | Hash for idempotent upserts |
| `updated_at` | TEXT | Last update timestamp |
| `row_hash` | TEXT | SHA-256 hash for cloud sync verification |

#### `sn_notes` — Individual footnotes (stock_notes)

**Why synced:** Footnote narratives and metadata are unique content extracted
from SEC filings. Re-extraction is expensive and may differ if filings are amended.

| Column | Type | Why |
|--------|------|-----|
| `note_id` | TEXT PRIMARY KEY | Composite: filing_id|N{number} |
| `filing_id` | TEXT | FK to sn_filings |
| `ticker` | TEXT | Stock ticker |
| `form` | TEXT | Form type |
| `accession_no` | TEXT | SEC accession number |
| `note_number` | INTEGER | Note number within the filing |
| `title` | TEXT | Note title |
| `short_name` | TEXT | Short display name |
| `narrative_text` | TEXT | Full narrative content |
| `narrative_hash` | TEXT | MD5 hash of narrative |
| `expands` | TEXT | JSON array of expandable references |
| `expands_statements` | TEXT | JSON array of statement references |
| `table_count` | INTEGER | Number of embedded tables |
| `details_count` | INTEGER | Number of detail (XBRL) tables |
| `quarter` | INTEGER | Fiscal quarter |
| `year` | INTEGER | Fiscal year |
| `quarterly_status` | TEXT | direct, from_annual_filing, etc. |
| `version` | INTEGER | Record version (for future conflict resolution) |
| `content_hash` | TEXT | Hash for idempotent upserts |
| `updated_at` | TEXT | Last update timestamp |
| `row_hash` | TEXT | SHA-256 hash for cloud sync verification |

#### `sn_detail_registry` — Registry of hydrated detail tables (stock_notes)

**Why synced:** Tracks which XBRL detail tables have been parsed into tidy format.
Losing this means re-hydrating all detail tables from scratch.

| Column | Type | Why |
|--------|------|-----|
| `registry_id` | TEXT PRIMARY KEY | MD5 hash of composite key |
| `ticker` | TEXT | Stock ticker |
| `detail_table_name` | TEXT | Generated table name |
| `source_title` | TEXT | Original detail table title |
| `source_note_number` | INTEGER | Parent note number |
| `source_accession_no` | TEXT | Parent filing accession number |
| `role_or_type` | TEXT | Role or type classification |
| `available_concepts` | TEXT | JSON array of XBRL concepts |
| `tidy_schema_version` | INTEGER | Schema version for migration |
| `row_count` | INTEGER | Number of tidy records |
| `quarter` | INTEGER | Fiscal quarter |
| `year` | INTEGER | Fiscal year |
| `quarterly_status` | TEXT | direct, from_annual_filing, etc. |
| `content_hash` | TEXT | Hash for idempotent upserts |
| `created_at` | TEXT | First insert timestamp |
| `updated_at` | TEXT | Last update timestamp |
| `row_hash` | TEXT | SHA-256 hash for cloud sync verification |

#### `sn_note_details` — Tidy-format XBRL detail records (stock_notes)

**Why synced:** The actual parsed footnote data in tidy (long) format. This is
the most expensive data to produce — it requires parsing complex XBRL tables
from EDGAR filings. Losing it means re-hydrating every note detail.

| Column | Type | Why |
|--------|------|-----|
| `detail_id` | TEXT PRIMARY Key | Composite: accession|note|detail|concept|period|row |
| `accession_no` | TEXT | SEC accession number |
| `note_number` | INTEGER | Parent note number |
| `detail_index` | INTEGER | Detail table index within note |
| `ticker` | TEXT | Stock ticker |
| `form` | TEXT | Form type |
| `concept` | TEXT | XBRL concept |
| `label` | TEXT | Human-readable label |
| `standard_concept` | TEXT | Standardized concept name |
| `level` | INTEGER | Nesting level |
| `abstract` | TEXT | "True" or "False" |
| `dimension` | TEXT | Dimension identifier |
| `is_breakdown` | TEXT | Whether this is a breakdown row |
| `dimension_axis` | TEXT | XBRL dimension axis |
| `dimension_member` | TEXT | XBRL dimension member |
| `dimension_member_label` | TEXT | Human-readable member label |
| `dimension_label` | TEXT | Human-readable dimension label |
| `balance` | TEXT | debit/credit |
| `weight` | TEXT | Weight value |
| `preferred_sign` | TEXT | Preferred sign |
| `parent_concept` | TEXT | Parent concept in hierarchy |
| `parent_abstract_concept` | TEXT | Parent abstract concept |
| `period_raw` | TEXT | Raw period string from XBRL |
| `period_end_date` | TEXT | Parsed period end date (YYYY-MM-DD) |
| `period_type` | TEXT | FY, Q1-Q4, YTD, H1-H2 |
| `value` | TEXT | The fact value (as string) |
| `row_order` | INTEGER | Row ordering within the table |
| `content_hash` | TEXT | MD5 hash for idempotent upserts |
| `extracted_at` | TEXT | When the data was extracted |
| `created_at` | TEXT | First insert timestamp |
| `updated_at` | TEXT | Last update timestamp |
| `row_hash` | TEXT | SHA-256 hash for cloud sync verification |

### Operational-Only Tables

These tables live only in Turso. They're either rebuilt on startup or contain
ephemeral operational data that doesn't need cloud backup.

#### `tool_runs` — Automatic execution history

**Why exists:** Every tool invocation is automatically recorded by the handler.
Provides "what did I run and what happened" without requiring `X-Observe`.

**Why operational-only:** Tool runs are high-volume ephemeral data.

| Column | Type | Why |
|--------|------|-----|
| `id` | INTEGER PRIMARY KEY | Row identifier |
| `tool_name` | TEXT | Which tool was run |
| `arguments_json` | TEXT | The input arguments as JSON |
| `result_json` | TEXT | The tool's output as JSON |
| `ok` | INTEGER | 1 = success, 0 = failure |
| `error` | TEXT | Error message if failed |
| `duration_ms` | REAL | Wall-clock time |
| `started_at` | TEXT | ISO-8601 start time |
| `completed_at` | TEXT | ISO-8601 end time |

#### `tools` — Tool registry

**Why exists:** Records which tools are registered, their schemas, and when
they were registered. Rebuilt every startup from discovered tool modules.

**Why operational-only:** The registry is rebuilt from code on every startup.

| Column | Type | Why |
|--------|------|-----|
| `id` | INTEGER PRIMARY KEY | Row identifier |
| `name` | TEXT UNIQUE | Tool name |
| `description` | TEXT | One-line description |
| `input_schema` | TEXT | JSON Schema |
| `registered_at` | TEXT | ISO-8601 timestamp |

### Telemetry Tables (local `telemetry.db`)

#### `activity_log` — High-volume Activity telemetry

**Why separate database:** Telemetry is high-volume, append-only, and only
relevant for debugging. Keeping it in a separate database prevents it from
interfering with operational data. It's never synced.

| Column | Type | Why |
|--------|------|-----|
| `id` | INTEGER PRIMARY KEY | Row identifier |
| `activity_name` | TEXT | Activity name |
| `input_data` | TEXT | Serialized input |
| `output_data` | TEXT | Serialized output |
| `error` | TEXT | Error message if failed |
| `ok` | INTEGER | 1 = success, 0 = failure |
| `duration_ms` | REAL | Wall-clock time |
| `started_at` | TEXT | ISO-8601 start time |
| `logged_at` | TEXT | ISO-8601 time when recorded |

## Why Migration Scripts Are Illegal

Traditional migration scripts are a **maintenance burden** and a **source of bugs**.
The **Expected Schema** (defined in `db/schema.py`) is the single source of truth.
On every startup, `SchemaManager` compares the actual database state against it:

| Drift Detected | Operational (Turso) | Cloud (Snowflake) |
|---|---|---|
| Missing table | Create it | Create it (synced tables only) |
| Missing column | Add with default value | Add with default value |
| Unexpected column | **Rebuild table** (backup → drop → recreate → repopulate) | **Rebuild table** (repopulate from Operational) |
| Unexpected table | **Drop it** | **Ignore it** (other systems may own it) |

## DualWriter — Write Patterns

The `DualWriter` supports three write patterns for cloud-synced tables:

| Method | SQL Pattern | Use Case |
|--------|-------------|----------|
| `write(table, data)` | INSERT | New rows with auto-generated IDs |
| `upsert(table, data)` | INSERT OR REPLACE | Composite-key tables (stock tools) |
| `delete(table, where, params)` | DELETE | Purge before refresh |
| `execute_on_both(sql, params)` | Raw SQL | Complex operations |

All methods automatically:
- Compute `row_hash` for synced tables
- Write to operational (Turso) first, using `BEGIN CONCURRENT` (with `BEGIN IMMEDIATE` fallback)
- Write to cloud (Snowflake) second
- Queue failed cloud writes for retry with exponential backoff

### Concurrent Transactions

Write transactions on Turso use `BEGIN CONCURRENT` for maximum concurrency.

pyturso wraps the **Turso Database Rust rewrite** (not libSQL), which natively
supports MVCC (Multi-Version Concurrency Control). The database is opened with
`PRAGMA journal_mode='mvcc'` to enable concurrent write transactions.

`BEGIN CONCURRENT` allows multiple write transactions to proceed simultaneously
using optimistic concurrency control with snapshot isolation. Conflict detection
happens at commit time (row-level). If a write-write conflict is detected, the
transaction receives `SQLITE_BUSY` and must be retried.

See the [Turso manual](https://github.com/tursodatabase/turso/blob/main/docs/manual.md)
for the full concurrent transaction lifecycle.

### Sync Verification

Synced tables use **row hashes** for verification. Each row has a `row_hash`
column containing a SHA-256 hash of all column values (except `id` and
`row_hash` itself).

Sync is verified on **startup only** (not shutdown). This prevents a faulty
run from overwriting healthy cloud data during the shutdown sequence.

## Connection Architecture

```
┌──────────────┐     ┌──────────────┐
│   Turso      │     │  Cloud       │
│  (Operational)│     │  (Snowflake  │
│              │     │   mock)      │
│ artifacts  ──┼─────┼── artifacts  │  ← synced (row_hash verified)
│ sf_tickers ──┼─────┼── sf_tickers │  ← synced (stock_financials)
│ sf_quarterly─┼─────┼── sf_quarterly│ ← synced (stock_financials)
│ sn_filings ──┼─────┼── sn_filings │  ← synced (stock_notes)
│ sn_notes   ──┼─────┼── sn_notes   │  ← synced (stock_notes)
│ sn_detail_r─┼─────┼── sn_detail_r│  ← synced (stock_notes)
│ sn_note_det─┼─────┼── sn_note_det│  ← synced (stock_notes)
│ tool_runs    │     │              │  ← operational only
│ tools        │     │              │  ← operational only
└──────┬───────┘     └──────┬───────┘
       │                    │
       └────────┬───────────┘
                │
         ┌──────┴───────┐
         │  DualWriter   │
         │  write/upsert │
         │  delete/retry │
         └──────────────┘
```

## Files

| File | Purpose |
|---|---|
| `config.py` | Loads `.env`, exposes all DB settings |
| `turso.py` | Turso connection (pyturso) — local or remote-synced |
| `cloud.py` | Snowflake mock connection (local pyturso) |
| `dual.py` | DualWriter — write/upsert/delete with hash + retry |
| `schema.py` | Expected Schema + SchemaManager (auto-heal) + CLOUD_SYNC_TABLES |
