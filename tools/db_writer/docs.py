# tools/db_writer/docs.py
TOOL_DESCRIPTION = "Save a record to the tool_runs table in the operational database. Demonstrates DB-connected tools."

TOOL_DOCS = """## DB Writer

Writes a JSON payload to the `tool_runs` table via the DualWriter. The record
is operational-only (not cloud-synced). Demonstrates how tools can interact
with the database layer.

### Activities

| # | Activity | Purpose |
|---|----------|---------|
| 1 | `db_writer.build_record` | Build the output record from input data |
| 2 | `db_writer.persist` | Write to operational DB via DualWriter |

If `persist` fails (server not initialized, DB error), the error is recorded
and the tool returns an error response.

### Input Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `data` | object | **yes** | Arbitrary JSON object to save |
| `label` | string | no | Human-readable label for the record |

**Request body (JSON):**
```json
{"data": {"key": "value", "count": 42}, "label": "test-run"}
```

**Headers:** None required. `X-Observe: true` returns Activity lineage.

### Expected Output

```json
{
  "saved": true,
  "label": "test-run",
  "data": {"key": "value", "count": 42},
  "record_count": 2,
  "processed_at": "2026-06-26T08:00:00+00:00",
  "db_row_id": 7
}
```

### Common Workflows

1. **Save tool output:** `{"data": {"result": 42, "source": "calculator"}, "label": "calc-run"}`
2. **Audit trail:** Use this tool to persist any intermediate results for later
   inspection via `GET /runs` or `GET /telemetry`.
3. **Data pipeline step:** Chain with other tools — save outputs from `textstats`
   or `crypto` as persistent records.

### Troubleshooting

- **"Server not initialized"** — The `db_writer.build_record` Activity succeeds,
  but `db_writer.persist` fails because the server's DualWriter isn't available.
  In the Lineage, you'll see Activity 1 succeeded and Activity 2 failed.
- **`record_count` is key count, not value count** — It counts top-level keys
  in the `data` object, not nested values. `{"a": {"b": 1}}` has
  `record_count: 1`.
- **No deduplication** — Calling this twice with the same data creates two
  records. There's no unique constraint on the content.
- **`data` must be a JSON object** — Arrays, strings, or primitives at the top
  level will be serialized but `record_count` uses `len()` which behaves
  differently for non-dict types (count of items/characters).
- **Operational-only** — This table is NOT cloud-synced. Data exists only in
  the local Turso database."""

TOOL_OUTPUT_EXAMPLE = {
    "saved": True,
    "label": "example",
    "data": {"key": "value"},
    "record_count": 1,
    "processed_at": "2026-06-26T08:00:00+00:00",
    "db_row_id": 1,
}
