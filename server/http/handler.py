# server/http/handler.py
"""
HTTP routes with long polling and observability support.

All endpoints:
  GET  /health              — quick health probe
  GET  /databases           — database health + schema status
  GET  /sync                — operational ↔ cloud sync status
  POST /resolve             — resolve sync conflict
  GET  /telemetry           — recent Activity telemetry
  GET  /artifacts           — list saved artifacts
  GET  /runs                — list recent tool runs
  POST /tools/{name}        — run a tool with long polling + optional lineage
  GET  /tools/{name}/docs   — tool documentation (markdown or JSON)
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import aiohttp.web

from server.accumulator import Accumulator
from tools import TOOL_REGISTRY
from db.schema import OPERATIONAL_TABLES, CLOUD_SYNC_TABLES

log = logging.getLogger("activity-server")

LONG_POLL_TIMEOUT = 3600


async def _run_tool(tool, arguments: dict, acc=None):
    """Run a tool, handling both sync and async execute()."""
    result = tool.execute(arguments, acc=acc)
    if asyncio.iscoroutine(result):
        return await result
    return result


async def handle_health(request):
    from server.app import get_server
    server = get_server()
    if server is None:
        return aiohttp.web.json_response({"status": "not_ready"}, status=503)
    return aiohttp.web.json_response({
        "status": "degraded" if server.tools_locked else "healthy",
        "tools_locked": server.tools_locked,
        "tools": list(server.tool_registry.keys()),
    })


async def handle_databases(request):
    """GET /databases — full database health report.

    Returns connection status, table details, schema state, and sync info
    for all three databases (operational, cloud, telemetry).
    """
    from server.app import get_server
    server = get_server()
    if server is None:
        return aiohttp.web.json_response({"status": "not_ready"}, status=503)

    def _db_info(conn, tables_dict, label):
        """Gather info for a single database."""
        info = {"status": "connected", "tables": {}}
        for tbl_name, expected in tables_dict.items():
            exists = server.schema_manager._table_exists(conn, tbl_name)
            if not exists:
                info["tables"][tbl_name] = {"exists": False, "row_count": 0, "columns": []}
                continue
            actual_cols = server.schema_manager._table_columns(conn, tbl_name)
            expected_cols = expected.col_names()
            row_count = server.schema_manager._count_rows(conn, tbl_name)
            unexpected = [c for c in actual_cols if c not in expected_cols]
            missing = [c for c in expected_cols if c not in actual_cols]
            info["tables"][tbl_name] = {
                "exists": True,
                "row_count": row_count,
                "columns": actual_cols,
                "unexpected_columns": unexpected,
                "missing_columns": missing,
                "schema_ok": len(unexpected) == 0 and len(missing) == 0,
            }
        return info

    from db.schema import OPERATIONAL_TABLES, CLOUD_SYNC_TABLES, TELEMETRY_TABLES

    operational = _db_info(server.turso, OPERATIONAL_TABLES, "operational")
    # Cloud only has synced tables
    cloud_tables = {k: v for k, v in OPERATIONAL_TABLES.items() if k in CLOUD_SYNC_TABLES}
    cloud = _db_info(server.cloud, cloud_tables, "cloud")
    telemetry = _db_info(server.tel_conn, TELEMETRY_TABLES, "telemetry")

    sync = server.schema_manager.check_sync()

    return aiohttp.web.json_response({
        "tools_locked": server.tools_locked,
        "operational": operational,
        "cloud": cloud,
        "telemetry": telemetry,
        "sync": sync,
        "in_sync": server.schema_manager.in_sync,
    })


async def handle_sync(request):
    from server.app import get_server
    server = get_server()
    if server is None:
        return aiohttp.web.json_response({"status": "not_ready"}, status=503)
    sync = server.schema_manager.check_sync()
    return aiohttp.web.json_response({
        "in_sync": server.schema_manager.in_sync, "tables": sync,
    })


async def handle_resolve(request):
    from server.app import get_server
    server = get_server()
    if server is None:
        return aiohttp.web.json_response({"status": "not_ready"}, status=503)
    body = await request.json()
    source = body.get("source", "")
    if source not in ("operational", "cloud"):
        return aiohttp.web.json_response(
            {"error": "source must be 'operational' or 'cloud'"}, status=400)
    server.resolve_sync(source)
    return aiohttp.web.json_response({
        "status": "resolved", "source": source,
        "tools_locked": server.tools_locked,
    })


async def handle_telemetry(request):
    from server.app import get_server
    server = get_server()
    if server is None:
        return aiohttp.web.json_response({"status": "not_ready"}, status=503)
    limit = int(request.query.get("limit", "50"))
    cur = server.tel_conn.execute(
        "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description] if rows else []
    return aiohttp.web.json_response({
        "count": len(rows),
        "entries": [dict(zip(cols, row)) for row in rows],
    })


async def handle_artifacts(request):
    """GET /artifacts — list saved artifacts with metadata."""
    from server.app import get_server
    server = get_server()
    if server is None:
        return aiohttp.web.json_response({"status": "not_ready"}, status=503)
    limit = int(request.query.get("limit", "50"))
    cur = server.turso.execute(
        "SELECT id, tool_name, filename, filepath, size_bytes, content_preview, created_at "
        "FROM artifacts ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description] if rows else []
    return aiohttp.web.json_response({
        "count": len(rows),
        "artifacts": [dict(zip(cols, row)) for row in rows],
    })


async def handle_runs(request):
    """GET /runs — list recent tool runs."""
    from server.app import get_server
    server = get_server()
    if server is None:
        return aiohttp.web.json_response({"status": "not_ready"}, status=503)
    limit = int(request.query.get("limit", "50"))
    tool_filter = request.query.get("tool", "")
    if tool_filter:
        cur = server.turso.execute(
            "SELECT * FROM tool_runs WHERE tool_name = ? ORDER BY id DESC LIMIT ?",
            (tool_filter, limit))
    else:
        cur = server.turso.execute(
            "SELECT * FROM tool_runs ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description] if rows else []
    return aiohttp.web.json_response({
        "count": len(rows),
        "runs": [dict(zip(cols, row)) for row in rows],
    })


async def handle_tool_run(request):
    """
    POST /tools/{name}
    Body: {"arguments": {...}}
    Query: ?timeout=3600
    Header: X-Observe: true  → includes lineage (ordered Activity data-flow)
    """
    from server.app import get_server
    server = get_server()
    if server and server.tools_locked:
        return aiohttp.web.json_response(
            {"error": "Server in degraded mode — tools locked. POST /resolve first."}, status=503)

    tool_name = request.match_info.get("name", "")
    tool = TOOL_REGISTRY.get(tool_name)
    if not tool:
        return aiohttp.web.json_response(
            {"error": f"Unknown tool: {tool_name}"}, status=404)

    try:
        body = await request.json()
    except Exception:
        body = {}
    arguments = body.get("arguments", {})

    observe = request.headers.get("X-Observe", "").lower() == "true"
    timeout = min(int(request.query.get("timeout", LONG_POLL_TIMEOUT)),
                  LONG_POLL_TIMEOUT)

    acc = Accumulator(disabled=not observe)
    start_time = time.time()
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        result = await asyncio.wait_for(
            _run_tool(tool, arguments, acc=acc), timeout=timeout)
        duration_ms = round((time.time() - start_time) * 1000, 2)
        completed_at = datetime.now(timezone.utc).isoformat()

        # Record tool run (operational only)
        if server and server.dual_writer:
            server.dual_writer.write("tool_runs", {
                "tool_name": tool_name,
                "arguments_json": json.dumps(arguments),
                "result_json": json.dumps(result),
                "ok": 1,
                "error": "",
                "duration_ms": duration_ms,
                "started_at": started_at,
                "completed_at": completed_at,
            })

    except asyncio.TimeoutError:
        return aiohttp.web.json_response(
            {"error": f"Tool timed out after {timeout}s"}, status=504)
    except Exception as e:
        duration_ms = round((time.time() - start_time) * 1000, 2)
        completed_at = datetime.now(timezone.utc).isoformat()

        # Record failed tool run
        if server and server.dual_writer:
            try:
                server.dual_writer.write("tool_runs", {
                    "tool_name": tool_name,
                    "arguments_json": json.dumps(arguments),
                    "result_json": "{}",
                    "ok": 0,
                    "error": str(e),
                    "duration_ms": duration_ms,
                    "started_at": started_at,
                    "completed_at": completed_at,
                })
            except Exception:
                pass  # Don't fail on recording failure

        if observe:
            return aiohttp.web.json_response({
                "error": str(e),
                "lineage": acc.lineage(),
            }, status=500)
        return aiohttp.web.json_response({"error": str(e)}, status=500)

    if observe:
        return aiohttp.web.json_response({
            "result": result,
            "lineage": acc.lineage(),
        })
    return aiohttp.web.json_response({"result": result})


async def handle_tool_docs(request):
    """GET /tools/{name}/docs — return tool documentation as markdown or JSON."""
    tool_name = request.match_info.get("name", "")
    tool = TOOL_REGISTRY.get(tool_name)
    if not tool:
        return aiohttp.web.json_response(
            {"error": f"Unknown tool: {tool_name}"}, status=404)

    accept = request.headers.get("Accept", "")
    if "application/json" in accept:
        return aiohttp.web.json_response(tool.docs())

    markdown = tool.docs_markdown()
    return aiohttp.web.Response(
        text=markdown,
        content_type="text/markdown",
        charset="utf-8",
    )
