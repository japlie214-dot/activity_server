# server/mcp/handler.py
"""
MCP JSON-RPC handler with long polling (1-hour timeout) and observability.

When X-Observe: true header is present, the response includes a lineage —
the ordered data-flow record of every Activity that fired during tool execution.
"""
import asyncio
import json
import logging

import aiohttp.web

from server.accumulator import Accumulator
from tools import TOOL_REGISTRY
from db.config import LONG_POLL_TIMEOUT

log = logging.getLogger("activity-server")


async def _run_tool(tool, arguments: dict, acc=None):
    """Run a tool — handles both sync and async execute()."""
    result = tool.execute(arguments, acc=acc)
    if asyncio.iscoroutine(result):
        return await result
    return result


async def handle_mcp(request: aiohttp.web.Request) -> aiohttp.web.Response:
    try:
        body = await request.json()
    except Exception:
        return _json_err(None, -32700, "Parse error")

    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    if method == "initialize":
        return _json_ok(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "activity-server", "version": "2.0.0"},
        })

    if method == "tools/list":
        tools = [{"name": t.name, "description": t.description,
                  "inputSchema": t.input_schema} for t in TOOL_REGISTRY.values()]
        return _json_ok(req_id, {"tools": tools})

    if method == "tools/call":
        from server.app import get_server
        server = get_server()
        if server and server.tools_locked:
            return _json_err(req_id, -32000,
                             "Server in degraded mode — tools locked.")

        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        tool = TOOL_REGISTRY.get(tool_name)
        if not tool:
            return _json_err(req_id, -32601, f"Unknown tool: {tool_name}")

        observe = request.headers.get("X-Observe", "").lower() == "true"
        timeout = min(int(request.query.get("timeout", str(LONG_POLL_TIMEOUT))),
                      LONG_POLL_TIMEOUT)

        acc = Accumulator(disabled=not observe)

        try:
            result = await asyncio.wait_for(
                _run_tool(tool, arguments, acc=acc), timeout=timeout)
        except asyncio.TimeoutError:
            return _json_err(req_id, -32000,
                             f"Tool timed out after {timeout}s")
        except Exception as e:
            if observe:
                return _json_ok(req_id, {
                    "content": [{"type": "text",
                                 "text": json.dumps({"error": str(e)}, indent=2)}],
                    "lineage": acc.lineage(),
                })
            return _json_err(req_id, -32000, str(e))

        response = {
            "content": [{"type": "text",
                          "text": json.dumps(result, indent=2)}],
        }
        if observe:
            response["lineage"] = acc.lineage()
        return _json_ok(req_id, response)

    return _json_err(req_id, -32601, f"Unknown method: {method}")


def _json_ok(req_id, result):
    return aiohttp.web.json_response(
        {"jsonrpc": "2.0", "id": req_id, "result": result})


def _json_err(req_id, code, message):
    return aiohttp.web.json_response(
        {"jsonrpc": "2.0", "id": req_id,
         "error": {"code": code, "message": message}})
