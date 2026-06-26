# tools/db_writer/tool.py
"""DB Writer tool — two Activities: build record, then persist."""
import json
from datetime import datetime, timezone
from tools import Tool
from server.accumulator import Activity
from .config import TOOL_NAME
from .docs import TOOL_DESCRIPTION, TOOL_DOCS, TOOL_OUTPUT_EXAMPLE


@Activity("db_writer.build_record")
def build_record(acc, data: dict, label: str) -> dict:
    """Build the output record from input data."""
    return {
        "saved": True,
        "label": label,
        "data": data,
        "record_count": len(data),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }


@Activity("db_writer.persist")
def persist(acc, data: dict, output: dict) -> int:
    """Write to operational DB via DualWriter. Returns row id."""
    from server.app import get_server
    server = get_server()
    if server is None:
        raise RuntimeError("Server not initialized")
    row_id = server.dual_writer.write("tool_runs", {
        "tool_name": "db_writer",
        "arguments_json": json.dumps(data),
        "result_json": json.dumps(output),
        "ok": 1,
        "error": "",
        "duration_ms": 0.0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    return row_id


class DBWriterTool(Tool):
    name = TOOL_NAME
    description = TOOL_DESCRIPTION
    input_schema = {
        "type": "object",
        "properties": {
            "data": {
                "type": "object",
                "description": "Arbitrary JSON data to save",
            },
            "label": {
                "type": "string",
                "description": "Optional label for the record",
            },
        },
        "required": ["data"],
    }

    def execute(self, arguments: dict, acc=None) -> dict:
        data = arguments["data"]
        label = arguments.get("label", "")
        output = build_record(acc, data, label)
        row_id = persist(acc, data, output)
        output["db_row_id"] = row_id
        return output

    @classmethod
    def docs(cls) -> dict:
        return {
            "summary": cls.description,
            "description": TOOL_DOCS,
            "input_schema": cls.input_schema,
            "output_example": TOOL_OUTPUT_EXAMPLE,
        }
