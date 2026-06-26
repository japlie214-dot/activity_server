# tools/calculator/tool.py
"""Calculator tool — two Activities: sanitize, then evaluate."""
import math
from tools import Tool
from server.accumulator import Activity
from .config import TOOL_NAME, SAFE_FUNCTIONS, SAFE_CONSTANTS, FORBIDDEN_TOKENS
from .docs import TOOL_DESCRIPTION, TOOL_DOCS, TOOL_OUTPUT_EXAMPLE


_SAFE = {f: getattr(math, f) for f in SAFE_FUNCTIONS if hasattr(math, f)}
_SAFE.update({"abs": abs, "round": round, "pow": pow, "min": min, "max": max})
_SAFE.update({c: getattr(math, c) for c in SAFE_CONSTANTS if hasattr(math, c)})


@Activity("calculator.sanitize")
def sanitize(acc, expression: str) -> str:
    """Validate expression against injection tokens. Returns cleaned expression."""
    for bad in FORBIDDEN_TOKENS:
        if bad in expression:
            raise ValueError(f"Forbidden token: {bad}")
    return expression.strip()


@Activity("calculator.evaluate")
def evaluate(acc, expression: str):
    """Evaluate the sanitized expression in a sandboxed namespace."""
    return eval(expression, {"__builtins__": {}}, _SAFE)


class CalculatorTool(Tool):
    name = TOOL_NAME
    description = TOOL_DESCRIPTION
    input_schema = {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "Math expression"}
        },
        "required": ["expression"],
    }

    def execute(self, arguments: dict, acc=None) -> dict:
        expr = sanitize(acc, arguments["expression"])
        result = evaluate(acc, expr)
        return {"expression": expr, "result": result}

    @classmethod
    def docs(cls) -> dict:
        return {
            "summary": cls.description,
            "description": TOOL_DOCS,
            "input_schema": cls.input_schema,
            "output_example": TOOL_OUTPUT_EXAMPLE,
        }
