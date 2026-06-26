# tools/healthcheck/tool.py
"""HealthCheck tool — two Activities: check sync, gather stats."""
from tools import Tool
from server.accumulator import Activity
from db.schema import OPERATIONAL_TABLES, CLOUD_SYNC_TABLES
from .config import TOOL_NAME
from .docs import TOOL_DESCRIPTION, TOOL_DOCS, TOOL_OUTPUT_EXAMPLE


@Activity("healthcheck.check_sync")
def check_sync(acc) -> tuple:
    """Check operational ↔ cloud sync status (synced tables only)."""
    from server.app import get_server
    server = get_server()
    if server is None:
        raise RuntimeError("Server not initialized")
    sync = server.schema_manager.check_sync()
    return sync, server.tools_locked


@Activity("healthcheck.gather_stats")
def gather_stats(acc) -> dict:
    """Gather row counts from all operational tables."""
    from server.app import get_server
    server = get_server()
    if server is None:
        raise RuntimeError("Server not initialized")
    counts = {}
    for tbl in OPERATIONAL_TABLES:
        cur = server.turso.execute(f"SELECT COUNT(*) FROM {tbl}")
        counts[tbl] = cur.fetchone()[0]
    return counts


class HealthCheckTool(Tool):
    name = TOOL_NAME
    description = TOOL_DESCRIPTION
    input_schema = {"type": "object", "properties": {}}

    def execute(self, arguments: dict, acc=None) -> dict:
        from server.app import get_server
        server = get_server()
        if server is None:
            return {"status": "error", "detail": "Server not initialized"}

        sync, tools_locked = check_sync(acc)
        op_counts = gather_stats(acc)

        return {
            "status": "degraded" if tools_locked else "healthy",
            "tools_locked": tools_locked,
            "registered_tools": list(server.tool_registry.keys()),
            "sync_status": sync,
            "operational_counts": op_counts,
            "started_at": server.started_at,
        }

    @classmethod
    def docs(cls) -> dict:
        return {
            "summary": cls.description,
            "description": TOOL_DOCS,
            "input_schema": cls.input_schema,
            "output_example": TOOL_OUTPUT_EXAMPLE,
        }
