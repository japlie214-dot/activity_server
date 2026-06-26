# tools/timestamp/docs.py
TOOL_DESCRIPTION = "Return current UTC timestamp in ISO-8601 and epoch."

TOOL_DOCS = """## Timestamp

Returns the current UTC time in two formats: ISO-8601 string and Unix epoch
(seconds since 1970-01-01T00:00:00Z). A pure utility with zero inputs.

### Activities

| # | Activity | Purpose |
|---|----------|---------|
| 1 | `timestamp.now` | Capture current UTC time in ISO-8601 and epoch |

Single Activity — no dependencies, no failure modes.

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
  "iso": "2026-06-26T08:00:00+00:00",
  "epoch": 1782547200.0
}
```

### Common Workflows

1. **Get current time:** `{}`
2. **Use as a building block:** Combine with other tools (e.g., generate a
   timestamp, then pass it to `db_writer` as metadata).
3. **Clock skew detection:** Compare the server's timestamp with your local
   time to detect clock drift.

### Troubleshooting

- **Epoch has decimal places** — Python's `datetime.timestamp()` returns a
  float. The fractional part is sub-second precision. If you need an integer,
  use `int(result["epoch"])`.
- **ISO string includes `+00:00`** — This is the UTC offset. It's always
  `+00:00` because the server uses `timezone.utc`. Some consumers expect `Z`
  suffix instead — both are valid ISO-8601.
- **No timezone conversion** — This tool returns UTC only. Convert to local
  time in your client code.
- **Slight delay between `iso` and `epoch`** — Both are captured from the same
  `datetime` object, so they represent the same instant. No skew between them."""

TOOL_OUTPUT_EXAMPLE = {
    "iso": "2026-06-26T08:00:00+00:00",
    "epoch": 1782547200.0,
}
