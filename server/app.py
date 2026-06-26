# server/app.py
"""
App assembly — wires lifecycle, routes, and MCP into an aiohttp Application.
"""
import aiohttp.web

from server.lifecycle import Server
from server.http.handler import (
    handle_health, handle_databases, handle_sync, handle_resolve,
    handle_telemetry, handle_artifacts, handle_runs,
    handle_tool_run, handle_tool_docs,
)
from server.mcp.handler import handle_mcp

_server: Server = None


def get_server() -> Server:
    return _server


def create_app(resolve_source=None) -> tuple:
    global _server
    _server = Server(resolve_source=resolve_source)
    _server.startup_sync()

    app = aiohttp.web.Application(client_max_size=1024*1024)
    app.router.add_post("/mcp",         handle_mcp)
    app.router.add_get("/health",       handle_health)
    app.router.add_get("/databases",    handle_databases)
    app.router.add_get("/sync",         handle_sync)
    app.router.add_post("/resolve",     handle_resolve)
    app.router.add_get("/telemetry",    handle_telemetry)
    app.router.add_get("/artifacts",    handle_artifacts)
    app.router.add_get("/runs",         handle_runs)
    app.router.add_get("/tools/{name}/docs", handle_tool_docs)
    app.router.add_post("/tools/{name}", handle_tool_run)
    app.router.add_get("/",             handle_health)

    return app, _server
