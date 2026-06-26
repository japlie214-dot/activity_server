<!-- tools/README.md -->
# Tools — Architecture, Contract, and How to Add New Tools

## Architecture: Activity → Accumulator → Lineage

Every tool decomposes its logic into a sequence of named **Activities**.
An **Accumulator** is created at the invocation boundary and threaded through
every Activity. When observability is active (`X-Observe: true`), the
Accumulator records each Activity's inputs, outputs, and errors and produces
a **Lineage** — the ordered data-flow record of the invocation.

```
HTTP Handler                    Tool execute()
  │                                │
  ├─ Accumulator(disabled=false)   │
  │                                │
  ├─ tool.execute(args, acc=acc) ──┤
  │                                │
  │       Activity 1 (sanitize) ◄──┤  acc.record(name, input, output)
  │              │                 │
  │       Activity 2 (evaluate) ◄──┤  acc.record(name, input, output)
  │              │                 │
  │       Activity 3 (persist)  ◄──┤  acc.record(name, input, output)
  │                                │
  ├─ acc.lineage() ────────────────┘
  │
  └─ Response: {result, lineage}
```

### Key Rules

1. **Activities are sequential.** Activity N must complete before Activity N+1 starts.
2. **Inside an Activity, async/threaded work is fine.** The Activity decorator
   handles both `sync def` and `async def` functions.
3. **Fail-hard.** If an Activity raises an exception, the error is recorded in
   the Lineage and re-raised. Subsequent Activities do NOT execute.
4. **The Lineage is a pure observation artifact.** It records what data entered
   and exited each Activity. It does not assert, validate, or judge.

### Zero-Overhead When Disabled

When `X-Observe` is not set, the Accumulator is created with `disabled=True`.
The `@Activity` decorator detects this and returns immediately — no recording,
no serialization, no overhead.

## Adding a New Tool (Step by Step)

### 1. Create the tool package

```
tools/
  my_tool/
    __init__.py     # just: # my_tool
    config.py       # TOOL_NAME and other constants
    desc.py         # TOOL_DESCRIPTION (separate file)
    tool.py         # implementation with Activity-decorated functions
```

### 2. Write `config.py`

```python
# tools/my_tool/config.py
TOOL_NAME = "my_tool"
```

### 3. Write `desc.py`

```python
# tools/my_tool/desc.py
TOOL_DESCRIPTION = "Does something useful."
```

### 4. Write `tool.py`

```python
# tools/my_tool/tool.py
from tools import Tool
from server.accumulator import Activity
from .config import TOOL_NAME
from .desc import TOOL_DESCRIPTION


@Activity("my_tool.validate")
def validate(acc, input: str) -> str:
    """Validate and clean the input."""
    if not input:
        raise ValueError("Input cannot be empty")
    return input.strip()


@Activity("my_tool.process")
def process(acc, cleaned: str) -> dict:
    """Process the validated input."""
    return {"result": cleaned.upper()}


class MyTool(Tool):
    name = TOOL_NAME
    description = TOOL_DESCRIPTION
    input_schema = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "The input"},
        },
        "required": ["input"],
    }

    def execute(self, arguments: dict, acc=None) -> dict:
        cleaned = validate(acc, arguments["input"])
        return process(acc, cleaned)

    @classmethod
    def docs(cls) -> dict:
        return {
            "summary": "One-line description.",
            "description": """## My Tool
            ...
            ### Activities
            | # | Activity | Purpose |
            |---|----------|---------|
            | 1 | my_tool.validate | Validate input |
            | 2 | my_tool.process | Process validated input |
            ...
            """,
            "input_schema": cls.input_schema,
            "output_example": {"result": "HELLO"},
        }
```

### 5. Done

`__init_subclass__` auto-registers the tool when the module is imported.
`load_all_tools()` in `run.py` discovers all `tools/*/tool.py` modules via `pkgutil`.

No registration step. No config file to edit. No import to add.

## Tool Interface

Every tool must:

- Inherit from `Tool` (in `tools/__init__.py`)
- Set `name` (string, unique) in `config.py`
- Set `description` (string) in `desc.py`
- Set `input_schema` (JSON Schema dict)
- Implement `execute(self, arguments: dict, acc=None) -> dict`
- Decompose logic into `@Activity`-decorated functions
- Implement `docs()` classmethod returning documentation dict

## The @Activity Decorator

`@Activity("name")` turns a function into a named, tracked Activity step.

```python
from server.accumulator import Activity

@Activity("calculator.sanitize")
def sanitize(acc, expression: str) -> str:
    ...
```

**Rules:**
- The function's first argument must be an `Accumulator` (conventionally named `acc`).
- Additional arguments are the Activity's inputs — they're recorded in the Lineage.
- The return value is the Activity's output — recorded in the Lineage.
- If the function raises, the error is recorded and re-raised (fail-hard).
- Works with both `def` and `async def`.

**What gets recorded:**

| Field | Source |
|-------|--------|
| `activity_id` | Auto-incrementing counter (1, 2, 3, ...) |
| `name` | The string passed to `@Activity("...")` |
| `input` | Bound arguments (excluding the Accumulator) |
| `output` | Return value (serialized) |
| `ok` | `true` if success, `false` if exception |
| `error` | `"ExceptionType: message"` or `null` |
| `duration_ms` | Wall-clock time in milliseconds |

## The Accumulator

```python
from server.accumulator import Accumulator

# At the handler boundary:
acc = Accumulator(disabled=not observe)

# Pass to tool:
result = tool.execute(arguments, acc=acc)

# Get the Lineage:
lineage = acc.lineage()
```

**Constructor:**
- `Accumulator(disabled=False)` — create an active accumulator
- `Accumulator(disabled=True)` — no-op, zero overhead

**Methods:**
- `acc.record(name, input_data, output_data, ok, error, ...)` — append a record
- `acc.lineage()` — return the ordered list of activity records as dicts

## The Lineage

The Lineage is the output of `acc.lineage()`. It's an ordered list of dicts,
one per Activity, in execution order:

```json
[
  {
    "activity_id": 1,
    "name": "calculator.sanitize",
    "input": {"expression": "2+2"},
    "output": "2+2",
    "ok": true,
    "error": null,
    "duration_ms": 0.05
  },
  {
    "activity_id": 2,
    "name": "calculator.evaluate",
    "input": {"expression": "2+2"},
    "output": 4,
    "ok": true,
    "error": null,
    "duration_ms": 0.02
  }
]
```

On failure, the Lineage shows which Activity failed and why. Subsequent
Activities do NOT appear because they were never executed:

```json
[
  {
    "activity_id": 1,
    "name": "calculator.sanitize",
    "input": {"expression": "import os"},
    "output": null,
    "ok": false,
    "error": "ValueError: Forbidden token: import",
    "duration_ms": 0.03
  }
]
```

## Sync vs Async Tools

Activities can be sync or async:

```python
# Sync Activity (CPU-bound)
@Activity("calculator.evaluate")
def evaluate(acc, expression: str):
    return eval(expression, {"__builtins__": {}}, _SAFE)

# Async Activity (I/O-bound or long-running)
@Activity("slow_hello.wait_and_respond")
async def wait_and_respond(acc, delay: int) -> dict:
    await asyncio.sleep(delay)
    return {"message": "Hello World"}
```

Both work. The server handles them correctly. Long-running async tools
benefit from long polling (up to 1-hour timeout).

## Long Polling

The server supports long polling with up to **1-hour timeout** for tools
that take a long time.

- **MCP**: `POST /mcp` with `tools/call` — query param `?timeout=3600`
- **HTTP**: `POST /tools/{name}` — query param `?timeout=3600`

The connection stays open until the tool completes or the timeout expires.
The `slow_hello` tool (240s delay) demonstrates this.

## Observability Header

By default, tool responses contain only the tool output.

When the `X-Observe: true` header is provided, the response also includes
a **lineage** — the ordered data-flow record of every Activity:

```json
{
  "result": {"expression": "2+2", "result": 4},
  "lineage": [
    {
      "activity_id": 1,
      "name": "calculator.sanitize",
      "input": {"expression": "2+2"},
      "output": "2+2",
      "ok": true,
      "error": null,
      "duration_ms": 0.05
    },
    {
      "activity_id": 2,
      "name": "calculator.evaluate",
      "input": {"expression": "2+2"},
      "output": 4,
      "ok": true,
      "error": null,
      "duration_ms": 0.02
    }
  ]
}
```

## Connecting Tools to the Database

Tools can read/write the operational database directly:

```python
@Activity("db_writer.persist")
def persist(acc, data: dict, output: dict) -> int:
    from server.app import get_server
    server = get_server()
    if server is None:
        raise RuntimeError("Server not initialized")
    row_id = server.dual_writer.write("tool_outputs", {...})
    return row_id
```

The `db_writer` tool demonstrates this pattern.

## Connecting Tools to File Artifacts

Tools can produce files in the `data/artifacts/` directory:

```python
@Activity("artifact_saver.write_file")
def write_file(acc, content: str, filename: str, artifacts_dir: Path) -> dict:
    filepath = artifacts_dir / filename
    filepath.write_text(content, encoding="utf-8")
    return {"filepath": str(filepath), "size_bytes": filepath.stat().st_size}
```

The `artifact_saver` tool demonstrates this.

## Built-in Tools

| Tool | Activities | Type | DB? | Files? |
|---|---|---|---|---|
| `calculator` | sanitize, evaluate | sync | no | no |
| `crypto` | prepare, hash | sync | no | no |
| `textstats` | count, hash | sync | no | no |
| `timestamp` | now | sync | no | no |
| `db_writer` | build_record, persist | sync | **writes** | no |
| `artifact_saver` | prepare, write_file, record_db | sync | **writes** | **writes** |
| `healthcheck` | check_sync, gather_stats | sync | reads | no |
| `slow_hello` | validate, wait_and_respond | **async** | no | no |
| `stock_financials` | validate, execute, format | sync | **reads/writes** | no |
| `stock_notes` | validate, execute, format | sync | **reads/writes** | no |

### Stock Tools — Database Integration

Both `stock_financials` and `stock_notes` use the `DualWriter` for all database
operations. Every write goes to both the operational database (Turso) and the
cloud backup (Snowflake) automatically.

**stock_financials** — 3 Activities:
1. `stock_financials.validate` — Parse and validate command + instructions
2. `stock_financials.execute` — Dispatch to extract/query/status/catalog
3. `stock_financials.format` — Normalize result into response dict

Commands: `extract` (fetch from EDGAR), `query` (read cached facts by
statement_type: income/balance/cashflow), `status` (cache stats),
`catalog` (available XBRL concepts).

**stock_notes** — 3 Activities:
1. `stock_notes.validate` — Parse and validate command + instructions
2. `stock_notes.execute` — Dispatch to discover/note/details
3. `stock_notes.format` — Normalize result into response dict

Commands: `discover` (list filings), `note` (list/drill into footnotes),
`details` (time-series for a specific XBRL concept).

**Refresh behavior:** Both tools support `refresh: true` to purge local + cloud
data and re-extract from EDGAR. `stock_notes` also accepts `force_refresh` as
a backward-compatible alias.

## Developer Contract — Documentation Requirement

Every tool **must** implement a `docs()` classmethod that returns a dict with
structured documentation.

### Required Fields

```python
@classmethod
def docs(cls) -> dict:
    return {
        "summary": "One-line description.",
        "description": "## Tool Name\n\nFull markdown...",
        "input_schema": cls.input_schema,
        "output_example": {"key": "value"},
    }
```

### What `description` Must Include

1. **Activities table** — list each Activity with its purpose
2. **Input schema** — every field in a table with type, required, description
3. **Expected output** — complete JSON example
4. **Explanation** — what the tool does and how it works
5. **Common workflows** — 2-3 realistic usage patterns
6. **Troubleshooting tips** — edge cases, unintuitive behavior, error messages

### Serving Documentation

- `GET /tools/{name}/docs` — returns markdown (default)
- `GET /tools/{name}/docs` with `Accept: application/json` — returns structured JSON
