# tools/calculator/config.py
# Calculator Tool Config
TOOL_NAME = "calculator"
TOOL_DESCRIPTION = "Evaluate a math expression. Supports +, -, *, /, **, sqrt, sin, cos, log."
SAFE_FUNCTIONS = ["sqrt", "abs", "round", "sin", "cos", "tan", "log", "pow", "min", "max"]
SAFE_CONSTANTS = ["pi", "e"]
FORBIDDEN_TOKENS = ["import", "exec", "eval", "__", "open", "os.", "sys.", "subprocess", "lambda"]
