<!-- README.md -->
# Activity Server

MCP tool host with dual-write telemetry pipeline using **Turso** (operational)
and **Snowflake** (cloud backup, mocked for development).

## Architecture: Activity → Accumulator → Lineage

Every entry point — HTTP handler, MCP call, CLI command, background job —
decomposes its business logic into a sequence of named **Activities**.

An **Accumulator** is created at the invocation boundary and threaded through
every Activity. When observability is active (`X-Observe: true`), the
Accumulator records each Activity's inputs, outputs, and errors and produces
a **Lineage** — the ordered data-flow record of the invocation.

```
Request → Handler → Accumulator → Tool.execute(args, acc)
                                        │
                         Activity 1 ◄───┤  acc.record(...)
                         Activity 2 ◄───┤  acc.record(...)
                         Activity 3 ◄───┤  acc.record(...)
                                        │
                         acc.lineage() ──┘ → Response
```

### Key Properties

| Property | Description |
|----------|-------------|
| **Sequential** | Activities execute in order. Activity N+1 starts only after N completes. |
| **Fail-hard** | If an Activity raises, the error is recorded and subsequent Activities are skipped. |
| **Observable** | With `X-Observe: true`, every Activity's input/output is captured in the Lineage. |
| **Zero overhead** | When observability is off, the Accumulator is a no-op. No recording, no serialization. |
| **Pure observation** | The Lineage records what happened. It does not assert, validate, or judge. |

### Example: Calculator Tool

The calculator decomposes into two Activities:

```
Activity 1: calculator.sanitize   →  Validate expression (no injection)
Activity 2: calculator.evaluate   →  Eval in sandboxed namespace
```

If `sanitize` raises `ValueError("Forbidden token: import")`, the Lineage
shows Activity 1 failed. Activity 2 never executes.

With `X-Observe: true`:
```json
{
  "result": {"expression": "2+2", "result": 4},
  "lineage": [
    {"activity_id": 1, "name": "calculator.sanitize",
     "input": {"expression": "2+2"}, "output": "2+2",
     "ok": true, "error": null, "duration_ms": 0.05},
    {"activity_id": 2, "name": "calculator.evaluate",
     "input": {"expression": "2+2"}, "output": 4,
     "ok": true, "error": null, "duration_ms": 0.02}
  ]
}
```

## Project Structure

```
activity_server/
├── .env                            # Configuration (TURSO_URL, ports, etc.)
├── run.py                          # Entry point
├── requirements.txt
├── README.md
├── test_server.py                  # Integration tests
│
├── db/                             # Database layer (independent of server)
│   ├── config.py                   #   Loads .env, all DB settings
│   ├── turso.py                    #   Turso connection (pyturso)
│   ├── cloud.py                    #   Snowflake mock (local pyturso)
│   ├── dual.py                     #   DualWriter (write-through + retry)
│   ├── schema.py                   #   Expected Schema + SchemaManager (auto-heal)
│   └── README.md                   #   Why migrations are illegal
│
├── server/                         # HTTP/MCP server framework
│   ├── app.py                      #   App assembly + singleton
│   ├── lifecycle.py                #   Startup / Shutdown orchestrator
│   ├── accumulator.py              #   Accumulator + @Activity decorator
│   ├── telemetry.py                #   Telemetry queue + ActivityCapture
│   ├── config/
│   │   └── loader.py               #   Server config (HOST, PORT)
│   ├── mcp/
│   │   └── handler.py              #   MCP JSON-RPC (long polling + lineage)
│   └── http/
│       └── handler.py              #   REST routes (long polling + lineage)
│
├── tools/                          # MCP tools — each in its own package
│   ├── __init__.py                 #   Tool base class + auto-registry
│   ├── README.md                   #   Architecture, contract, how to add tools
│   ├── calculator/                 #   Safe math eval (2 Activities)
│   ├── crypto/                     #   SHA-256 hashing (2 Activities)
│   ├── healthcheck/                #   Server health + sync status (2 Activities)
│   ├── timestamp/                  #   UTC time (1 Activity)
│   ├── textstats/                  #   Text analysis (2 Activities)
│   ├── db_writer/                  #   Writes output to DB (2 Activities)
│   ├── artifact_saver/             #   Saves .txt file + DB record (3 Activities)
│   └── slow_hello/                 #   240s delay — long polling demo (2 Activities)
│
└── data/                           # Auto-created database files + artifacts
    ├── operational.db              #   Turso (pyturso)
    ├── cloud_snowflake.db          #   Snowflake mock (pyturso)
    ├── telemetry.db                #   Activity log (local only)
    └── artifacts/                  #   Tool-generated files
```

## Quick Start

```bash
pip install -r requirements.txt
python run.py --port 8080
```

### Remote Turso

```bash
# In .env:
TURSO_URL=https://your-db.your-region.turso.io
TURSO_AUTH_TOKEN=your-token-here
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/mcp` | MCP JSON-RPC (initialize, tools/list, tools/call) |
| `POST` | `/tools/{name}` | Run a tool via REST (long polling supported) |
| `GET` | `/tools/{name}/docs` | Tool documentation (markdown or JSON) |
| `GET` | `/health` | Quick health probe |
| `GET` | `/databases` | Full database health + schema status |
| `GET` | `/sync` | Synced tables sync status |
| `POST` | `/resolve` | Resolve sync conflict (operational or cloud as source) |
| `GET` | `/artifacts` | List saved artifacts with metadata |
| `GET` | `/runs` | List tool execution history (filterable with `?tool=name`) |
| `GET` | `/telemetry?limit=N` | Recent Activity telemetry |

## Features

### Activity Decomposition

Every tool breaks its logic into named Activities. Each Activity does one thing.
Activities are sequential — the next one starts only after the previous completes.
Inside an Activity, async or threaded work is fine.

```python
@Activity("calculator.sanitize")
def sanitize(acc, expression: str) -> str:
    for bad in FORBIDDEN_TOKENS:
        if bad in expression:
            raise ValueError(f"Forbidden token: {bad}")
    return expression.strip()

@Activity("calculator.evaluate")
def evaluate(acc, expression: str):
    return eval(expression, {"__builtins__": {}}, _SAFE)

class CalculatorTool(Tool):
    def execute(self, arguments: dict, acc=None) -> dict:
        expr = sanitize(acc, arguments["expression"])
        result = evaluate(acc, expr)
        return {"expression": expr, "result": result}
```

### Observability (Lineage)

Add `X-Observe: true` header to any tool call. The response includes a
**lineage** — the ordered data-flow of every Activity:

```bash
curl -X POST http://localhost:8080/tools/calculator \
  -H "Content-Type: application/json" \
  -H "X-Observe: true" \
  -d '{"arguments": {"expression": "2+2"}}'
```

Response:
```json
{
  "result": {"expression": "2+2", "result": 4},
  "lineage": [
    {"activity_id": 1, "name": "calculator.sanitize",
     "input": {"expression": "2+2"}, "output": "2+2",
     "ok": true, "error": null, "duration_ms": 0.05},
    {"activity_id": 2, "name": "calculator.evaluate",
     "input": {"expression": "2+2"}, "output": 4,
     "ok": true, "error": null, "duration_ms": 0.02}
  ]
}
```

On failure, the Lineage shows which Activity failed. Subsequent Activities
are NOT executed (fail-hard) and do NOT appear in the Lineage.

### Long Polling
Tools can run for up to 1 hour. The connection stays open until completion:
```
POST /tools/slow_hello?timeout=600
```

### Dual-Write
Every write goes to Turso (operational) AND Snowflake (cloud). If cloud fails,
writes are queued and retried with exponential backoff.

### Auto-Heal Schema
No migration scripts. The Expected Schema in `db/schema.py` is the single source
of truth. On startup, the server auto-repairs any drift. See `db/README.md`.

### Auto-Registered Tools
Subclass `Tool`, set `name`, implement `execute()`, decompose into `@Activity`
functions. Done. No registration step needed. See `tools/README.md`.
