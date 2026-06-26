# tools/slow_hello/docs.py
TOOL_DESCRIPTION = "Outputs 'Hello World' after 240 seconds. Demonstrates long-running tool with long polling."

TOOL_DOCS = """## Slow Hello

An **async** tool that sleeps for a configurable duration before returning.
Default delay is 240 seconds. Designed to demonstrate the server's long polling
capability — the HTTP connection stays open until the tool completes.

### Activities

| # | Activity | Purpose |
|---|----------|---------|
| 1 | `slow_hello.validate` | Validate and normalize the delay parameter |
| 2 | `slow_hello.wait_and_respond` | Sleep for the delay, then return the greeting |

Activity 2 is **async** — it uses `asyncio.sleep()` internally, so other
server tasks can run concurrently while this tool waits.

### Input Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `delay` | integer | no | Delay in seconds (default: 240) |

**Request body (JSON):**
```json
{"delay": 5}
```

**Headers:** None required. `X-Observe: true` returns Activity lineage
(shows the wait time in `slow_hello.wait_and_respond` duration).

### Expected Output

```json
{
  "message": "Hello World",
  "delay_seconds": 5,
  "started_at": "2026-06-26T08:00:00+00:00",
  "completed_at": "2026-06-26T08:00:05+00:00"
}
```

### Common Workflows

1. **Quick test:** `{"delay": 1}` — returns in ~1 second.
2. **Long poll test:** `{"delay": 240}` — default, tests 4-minute connection.
3. **Timeout test:** `{"delay": 3601}` — will hit the 1-hour server timeout.

### Troubleshooting

- **Connection times out** — The server has a 1-hour (3600s) timeout for long
  polling. If `delay > 3600`, you'll get a 504 timeout error. The tool itself
  keeps running on the server, but the client connection is severed.
- **No partial results** — There's no streaming or progress updates. The
  response is sent only after the full delay completes. If you need progress,
  use `X-Observe: true` and check the Lineage after completion.
- **`delay` defaults to 240, not 0** — If you omit `delay`, the tool waits
  240 seconds. This is intentional for demo purposes. Always pass
  `{"delay": N}` explicitly in production-like usage.
- **Negative delay** — The `slow_hello.validate` Activity normalizes negative
  values to 0. The tool returns immediately.
- **Multiple concurrent calls** — Each call is independent. They run in
  parallel as async tasks. 10 calls with `{"delay": 5}` complete in ~5s total,
  not 50s.
- **This tool is async** — Unlike other tools, `wait_and_respond` uses
  `async def`. The server handles this correctly via `asyncio.wait_for()`.
  The `slow_hello.validate` Activity is sync (fast validation)."""

TOOL_OUTPUT_EXAMPLE = {
    "message": "Hello World",
    "delay_seconds": 240,
    "started_at": "2026-06-26T08:00:00+00:00",
    "completed_at": "2026-06-26T08:04:00+00:00",
}
