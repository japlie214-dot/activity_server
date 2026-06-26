<!-- db/README.md -->
# Database Layer

## Schema Design — Justification

### Cloud-Synced Tables (`CLOUD_SYNC_TABLES`)

Only tables with irreplaceable user-created content are synced to Snowflake.
Each synced table has a `row_hash` column for content-addressable sync verification.

#### `artifacts` — File artifacts produced by tools

**Why synced:** Files are the actual user-created work product. Losing them
means losing work. Cloud backup ensures durability.

| Column | Type | Why |
|--------|------|-----|
| `id` | INTEGER PRIMARY KEY | Row identifier, used for hash comparison across databases |
| `tool_name` | TEXT | Which tool created this artifact. Enables filtering by source |
| `filename` | TEXT | Human-readable name. Used for display and `GET /artifacts` listing |
| `filepath` | TEXT | Absolute path on disk. Needed to locate the actual file |
| `size_bytes` | INTEGER | File size without reading the file. Useful for capacity planning and display |
| `content_preview` | TEXT | First 200 chars of content. Enables quick identification without reading the file |
| `created_at` | TEXT | ISO-8601 timestamp. Needed for sorting, filtering, and "when was this created?" |
| `row_hash` | TEXT | SHA-256 hash of row content (excluding id and row_hash). Enables O(n) sync verification instead of comparing every column |

### Operational-Only Tables

These tables live only in Turso. They're either rebuilt on startup or contain
ephemeral operational data that doesn't need cloud backup.

#### `tool_runs` — Automatic execution history

**Why exists:** Every tool invocation is automatically recorded by the handler.
Provides "what did I run and what happened" without requiring `X-Observe`.
This is the operational audit trail — lightweight, always-on, no opt-in needed.

**Why operational-only:** Tool runs are high-volume ephemeral data. They're
useful for debugging and history but not irreplaceable. Cloud backup would
waste storage on data that's only relevant短期.

| Column | Type | Why |
|--------|------|-----|
| `id` | INTEGER PRIMARY KEY | Row identifier |
| `tool_name` | TEXT | Which tool was run. Enables filtering: "show me all calculator runs" |
| `arguments_json` | TEXT | The input arguments as JSON. Needed to reproduce or understand the run |
| `result_json` | TEXT | The tool's output as JSON. Needed to see what happened |
| `ok` | INTEGER | 1 = success, 0 = failure. Enables quick filtering of failures |
| `error` | TEXT | Error message if failed. Empty string on success. Needed for debugging |
| `duration_ms` | REAL | Wall-clock time. Enables performance monitoring and slow-tool detection |
| `started_at` | TEXT | ISO-8601 start time. Needed for timeline reconstruction |
| `completed_at` | TEXT | ISO-8601 end time. Needed for timeline reconstruction |

#### `tools` — Tool registry

**Why exists:** Records which tools are registered, their schemas, and when
they were registered. Rebuilt every startup from discovered tool modules.

**Why operational-only:** The registry is rebuilt from code on every startup.
Cloud backup is pointless — the source of truth is the codebase itself.

| Column | Type | Why |
|--------|------|-----|
| `id` | INTEGER PRIMARY KEY | Row identifier |
| `name` | TEXT UNIQUE | Tool name. The primary lookup key for tool discovery |
| `description` | TEXT | One-line description. Used in health checks and listings |
| `input_schema` | TEXT | JSON Schema. Needed by MCP `tools/list` to describe tool inputs |
| `registered_at` | TEXT | ISO-8601 timestamp. Useful for "when did this tool appear?" |

### Telemetry Tables (local `telemetry.db`)

#### `activity_log` — High-volume Activity telemetry

**Why separate database:** Telemetry is high-volume, append-only, and only
relevant for debugging. Keeping it in a separate database prevents it from
interfering with operational data. It's never synced.

| Column | Type | Why |
|--------|------|-----|
| `id` | INTEGER PRIMARY KEY | Row identifier |
| `activity_name` | TEXT | Activity name (e.g., `tool.calculator`, `schema.check_sync`). Enables filtering |
| `input_data` | TEXT | Serialized input. Needed to understand what triggered the activity |
| `output_data` | TEXT | Serialized output. Needed to see the result |
| `error` | TEXT | Error message if failed. Empty on success |
| `ok` | INTEGER | 1 = success, 0 = failure. Quick filter for failures |
| `duration_ms` | REAL | Wall-clock time. Performance monitoring |
| `started_at` | TEXT | ISO-8601 start time |
| `logged_at` | TEXT | ISO-8601 time when the telemetry was recorded (may differ from started_at due to queue drain delay) |

### Removed Tables

- **`users`** — Not needed. This is a tool host, not a user management system.
- **`transactions`** — Not needed. Was a demo table with no real purpose.

## Why Migration Scripts Are Illegal

Traditional migration scripts (`001_add_column.sql`, `002_create_table.sql`, etc.)
are a **maintenance burden** and a **source of bugs**. Here's why this project
uses auto-heal instead:

### Problems with migrations

1. **Ordering nightmares**: Which migration ran last? What if someone skipped one?
2. **Environment drift**: Dev, staging, and production databases diverge silently.
3. **Rollback complexity**: Down migrations are fragile and often untested.
4. **Human error**: Forgetting to run a migration after pulling code = broken app.
5. **Merge conflicts**: Two developers adding columns = conflicting migration files.
6. **Brittle deploys**: Deployment scripts must know the exact migration sequence.

### The auto-heal alternative

The **Expected Schema** (defined in `db/schema.py`) is the single source of truth.
On every startup, `SchemaManager` compares the actual database state against it:

| Drift Detected | Operational (Turso) | Cloud (Snowflake) |
|---|---|---|
| Missing table | Create it | Create it (synced tables only) |
| Missing column | Add with default value | Add with default value |
| Unexpected column | **Rebuild table** (see below) | **Rebuild table** |
| Unexpected table | **Drop it** | **Leave it** (other systems may own it) |

### Table rebuild (unexpected columns)

When a table has columns that aren't in the expected schema, the SchemaManager
performs a non-destructive rebuild:

1. **Backup** all existing rows
2. **Drop** the table
3. **Recreate** with the expected schema
4. **Repopulate** from backup (matching columns only)
5. **Compute** values for new columns that have `compute` functions

This handles schema evolution gracefully:
- Old columns not in the new schema → silently dropped from the backup
- New columns in the schema not in the backup → filled with computed values or defaults
- The `row_hash` column uses a `compute` function that hashes all other columns

```python
Col("row_hash", "TEXT", "''",
    compute=lambda row: hashlib.sha256(
        ";".join(f"{k}={v}" for k, v in sorted(row.items())
                 if k not in {"id", "row_hash"}
        ).encode()
    ).hexdigest()[:16]),
```

This means you can add a new column with complex computation logic (hashes,
derived values, lookups) and existing data will be filled correctly on the
next startup.

### How it works

1. Define your schema in Python: `Col("name", "TEXT", "''")`
2. On startup, `SchemaManager.validate_operational()` runs
3. It queries `PRAGMA table_info()` for each expected table
4. Missing columns get `ALTER TABLE ADD COLUMN ... DEFAULT ...`
5. Missing tables get `CREATE TABLE`
6. Unexpected tables get `DROP TABLE`
7. Done. No scripts, no ordering, no human intervention.

### Default values matter

When adding a column, the default value fills existing rows. Design defaults carefully:

```python
Col("size_bytes",   "INTEGER", "0")      # numeric zero
Col("created_at",   "TEXT",    "''")      # empty string, not NULL
Col("row_hash",     "TEXT",    "''")      # empty string — will be computed on next write
Col("ok",           "INTEGER", "1")       # boolean as int
```

### Cloud-specific behavior

The cloud (Snowflake) only contains synced tables. Unexpected tables in cloud
are left alone because other systems may own them.

## Sync Verification

Synced tables use **row hashes** for verification. Each row has a `row_hash`
column containing a SHA-256 hash of all column values (except `id` and
`row_hash` itself).

On startup, the SchemaManager compares hashes between operational and cloud:
- Same id, same hash → in sync
- Same id, different hash → data diverged
- Id exists in one but not other → row missing

This is O(n) instead of comparing every column of every row.

```
┌──────────────┐     ┌──────────────┐
│   Turso      │     │  Cloud       │
│  (Operational)│     │  (Snowflake  │
│              │     │   mock)      │
│ artifacts  ──┼─────┼── artifacts  │  ← synced (row_hash verified)
│ tool_runs    │     │              │  ← operational only
│ tools        │     │              │  ← operational only
└──────┬───────┘     └──────┬───────┘
       │                    │
       └────────┬───────────┘
                │
         ┌──────┴───────┐
         │  DualWriter   │
         │  (write-thru  │
         │   + hash)     │
         └──────────────┘
```

## Connection Architecture

Every write goes to Operational first. If the table is in `CLOUD_SYNC_TABLES`,
the write also goes to Cloud with an automatic `row_hash`. If Cloud fails,
the write is queued for retry with exponential backoff.

## Files

| File | Purpose |
|---|---|
| `config.py` | Loads `.env`, exposes all DB settings |
| `turso.py` | Turso connection (pyturso) — local or remote-synced |
| `cloud.py` | Snowflake mock connection (local pyturso) |
| `dual.py` | DualWriter — write-through with hash + retry |
| `schema.py` | Expected Schema + SchemaManager (auto-heal) + CLOUD_SYNC_TABLES |
