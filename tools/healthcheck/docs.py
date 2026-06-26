# tools/healthcheck/docs.py
TOOL_DESCRIPTION = "Return server health, DB sync status, and telemetry summary."

TOOL_DOCS = """## HealthCheck

Returns a comprehensive health report: server status, database synchronization
state between operational (Turso) and cloud (Snowflake), registered tool list,
and row counts for all operational tables. No inputs required.

### Activities

| # | Activity | Purpose |
|---|----------|---------|
| 1 | `healthcheck.check_sync` | Check operational ↔ cloud sync status (synced tables only) |
| 2 | `healthcheck.gather_stats` | Gather row counts from all operational tables |

If either Activity fails (server not initialized), the error is recorded and
the tool returns an error response.

### Input Schema

None. This tool takes no parameters.

**Request body (JSON):**
```json
{}
```

**Headers:** None required. `X-Observe: true` returns Activity lineage.

### Expected Output

```json
{
  "status": "healthy",
  "tools_locked": false,
  "registered_tools": ["calculator", "crypto", "timestamp", "textstats",
                        "db_writer", "artifact_saver", "healthcheck", "slow_hello"],
  "sync_status": {
    "artifacts": {"op_count": 5, "cloud_count": 5, "match": true, "hash_mismatches": 0}
  },
  "operational_counts": {
    "artifacts": 5,
    "tool_runs": 12,
    "tools": 8
  },
  "started_at": "2026-06-26T08:00:00+00:00"
}
```

### Status Values

| Status | Meaning |
|--------|---------|
| `healthy` | All systems operational, synced tables match |
| `degraded` | Synced tables out of sync — tools are locked until resolved |
| `error` | Server not initialized (should not happen in normal operation) |

### Common Workflows

1. **Quick health probe:** Use `GET /health` instead for a lightweight check.
2. **Full diagnostic:** Call this tool when investigating issues — it returns
   sync status and row counts for every table.
3. **Monitoring:** Poll periodically and alert on `status != "healthy"`.

### Troubleshooting

- **`status: "degraded"` and `tools_locked: true`** — The synced tables
  (artifacts) are out of sync between operational and cloud. Resolve by
  calling `POST /resolve` with `{"source": "operational"}` or
  `{"source": "cloud"}` to pick which DB wins.
- **`sync_status` shows `hash_mismatches > 0`** — Row hashes differ between
  operational and cloud. This means data diverged. Use `POST /resolve` to
  reconcile.
- **`status: "error"`** — The server singleton isn't initialized. Check if
  the server started correctly. In the Lineage, you'll see
  `healthcheck.check_sync` failed.
- **Tool list doesn't match expected** — Tools are discovered via `pkgutil`.
  If a tool is missing, check that its `tool.py` exists and its class inherits
  from `Tool` with a non-empty `name`.
- **Row counts are 0 for all tables** — Normal for a fresh database. Counts
  grow as tools like `db_writer` and `artifact_saver` are used.
- **Only `artifacts` appears in sync_status** — Only cloud-synced tables
  (defined in `CLOUD_SYNC_TABLES`) are checked for sync. `tool_runs` and
  `tools` are operational-only."""

TOOL_OUTPUT_EXAMPLE = {
    "status": "healthy",
    "tools_locked": False,
    "registered_tools": ["calculator", "crypto", "timestamp"],
    "sync_status": {},
    "operational_counts": {},
    "started_at": "2026-06-26T08:00:00+00:00",
}
