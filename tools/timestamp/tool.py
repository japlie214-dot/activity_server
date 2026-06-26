# tools/timestamp/tool.py
"""Timestamp tool — one Activity: get current time."""
from datetime import datetime, timezone
from tools import Tool
from server.accumulator import Activity
from .config import TOOL_NAME
from .docs import TOOL_DESCRIPTION, TOOL_DOCS, TOOL_OUTPUT_EXAMPLE


@Activity("timestamp.now")
def now(acc) -> dict:
    """Capture the current UTC time in ISO-8601 and epoch formats."""
    dt = datetime.now(timezone.utc)
    return {"iso": dt.isoformat(), "epoch": dt.timestamp()}


class TimestampTool(Tool):
    name = TOOL_NAME
    description = TOOL_DESCRIPTION
    input_schema = {"type": "object", "properties": {}}

    def execute(self, arguments: dict, acc=None) -> dict:
        return now(acc)

    @classmethod
    def docs(cls) -> dict:
        return {
            "summary": cls.description,
            "description": TOOL_DOCS,
            "input_schema": cls.input_schema,
            "output_example": TOOL_OUTPUT_EXAMPLE,
        }
