# tools/slow_hello/tool.py
"""Slow Hello tool — two Activities: validate, then wait and respond."""
import asyncio
from datetime import datetime, timezone
from tools import Tool
from server.accumulator import Activity
from .config import TOOL_NAME, DELAY_SECONDS
from .docs import TOOL_DESCRIPTION, TOOL_DOCS, TOOL_OUTPUT_EXAMPLE


@Activity("slow_hello.validate")
def validate(acc, delay) -> int:
    """Validate and normalize the delay parameter."""
    if delay is None:
        return DELAY_SECONDS
    delay = int(delay)
    if delay < 0:
        return 0
    return delay


@Activity("slow_hello.wait_and_respond")
async def wait_and_respond(acc, delay: int) -> dict:
    """Sleep for the specified delay, then return the greeting."""
    start = datetime.now(timezone.utc)
    await asyncio.sleep(delay)
    end = datetime.now(timezone.utc)
    return {
        "message": "Hello World",
        "delay_seconds": delay,
        "started_at": start.isoformat(),
        "completed_at": end.isoformat(),
    }


class SlowHelloTool(Tool):
    name = TOOL_NAME
    description = TOOL_DESCRIPTION
    input_schema = {
        "type": "object",
        "properties": {
            "delay": {
                "type": "integer",
                "description": f"Override delay in seconds (default: {DELAY_SECONDS})",
            },
        },
    }

    def execute(self, arguments: dict, acc=None) -> dict:
        delay = validate(acc, arguments.get("delay", DELAY_SECONDS))
        return wait_and_respond(acc, delay)

    @classmethod
    def docs(cls) -> dict:
        return {
            "summary": cls.description,
            "description": TOOL_DOCS,
            "input_schema": cls.input_schema,
            "output_example": TOOL_OUTPUT_EXAMPLE,
        }
