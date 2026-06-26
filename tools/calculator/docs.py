# tools/calculator/docs.py
TOOL_DESCRIPTION = "Evaluate a math expression. Supports +, -, *, /, **, sqrt, sin, cos, log."

TOOL_DOCS = """## Calculator

Evaluates a math expression string using a sandboxed `eval()` with a restricted
namespace. Only whitelisted math functions and constants are available — no
file I/O, no imports, no attribute access.

### Activities

| # | Activity | Purpose |
|---|----------|---------|
| 1 | `calculator.sanitize` | Validate expression against injection tokens |
| 2 | `calculator.evaluate` | Evaluate the sanitized expression |

If `sanitize` fails (forbidden token), `evaluate` never runs.

### Input Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `expression` | string | **yes** | Math expression to evaluate |

**Request body (JSON):**
```json
{"expression": "sqrt(144) + 2**10"}
```

**Headers:** None required. `X-Observe: true` returns Activity lineage.

### Expected Output

```json
{
  "expression": "sqrt(144) + 2**10",
  "result": 1036.0
}
```

On error (forbidden token):
```json
{
  "expression": "import os",
  "error": "Forbidden token: import"
}
```

### Available Functions & Constants

**Functions:** `sqrt`, `abs`, `round`, `sin`, `cos`, `tan`, `log`, `pow`, `min`, `max`
**Constants:** `pi`, `e`
**Operators:** `+`, `-`, `*`, `/`, `**`, `%`, `//`

### Common Workflows

1. **Quick calculation:** `{"expression": "2 * pi * 10"}`
2. **Unit conversion:** `{"expression": "100 * 1.60934"}` (miles → km)
3. **Compound interest:** `{"expression": "1000 * (1 + 0.05) ** 12"}`

### Troubleshooting

- **"Forbidden token: import"** — The expression contains a blocked keyword.
  This is intentional: the calculator runs in a sandboxed `eval()` and rejects
  anything that looks like code injection. Keep expressions to pure math.
  The `sanitize` Activity raises `ValueError` — the `evaluate` Activity never runs.
- **"Forbidden token: __"** — Double underscores are blocked to prevent
  dunder-method access (e.g., `__import__`). This is a security feature, not a bug.
- **"name 'x' is not defined"** — Only whitelisted names are available.
  You cannot define variables or use arbitrary Python names. The `evaluate`
  Activity runs in a restricted namespace with only math functions.
- **Result is `int` vs `float`** — `2 + 2` returns `4` (int), `2 + 2.0` returns `4.0`
  (float). This is standard Python behavior.
- **Precision:** Results use Python's native float precision (~15 significant digits).
  For financial calculations, consider rounding explicitly: `round(expr, 2)`.
- **Lineage shows `calculator.sanitize` failed** — The error is recorded in the
  Lineage with `ok: false`. The `calculator.evaluate` Activity will NOT appear
  in the Lineage because it was never executed (fail-hard)."""

TOOL_OUTPUT_EXAMPLE = {
    "expression": "sqrt(144) + 2**10",
    "result": 1036.0,
}
