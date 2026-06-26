#!/usr/bin/env python3
# run.py
"""
Activity Server — entry point.

Usage:
  python run.py [--port 8080] [--resolve-operational] [--resolve-cloud]
"""
import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import aiohttp.web

from server.config.loader import HOST, PORT, PLATFORM
from tools import load_all_tools
from server.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("activity-server")


def main():
    parser = argparse.ArgumentParser(description="Activity MCP Server")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--resolve-operational", action="store_true")
    parser.add_argument("--resolve-cloud", action="store_true")
    args = parser.parse_args()

    resolve = None
    if args.resolve_operational:
        resolve = "operational"
    elif args.resolve_cloud:
        resolve = "cloud"

    load_all_tools()
    app, server = create_app(resolve_source=resolve)
    loop = asyncio.new_event_loop()

    loop.run_until_complete(server.post_startup())

    runner = aiohttp.web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = aiohttp.web.TCPSite(runner, HOST, args.port)
    loop.run_until_complete(site.start())

    log.info(f"Listening on http://{HOST}:{args.port}")
    log.info(f"  MCP:       POST /mcp")
    log.info(f"  Health:    GET  /health")
    log.info(f"  Databases: GET  /databases")
    log.info(f"  Sync:      GET  /sync")
    log.info(f"  Resolve:   POST /resolve")
    log.info(f"  Telemetry: GET  /telemetry")
    log.info(f"  Artifacts: GET  /artifacts")
    log.info(f"  Runs:      GET  /runs")
    log.info(f"  Tools:     POST /tools/{{name}}")
    log.info(f"  Tool Docs: GET  /tools/{{name}}/docs")
    log.info(f"  Long poll: up to 1 hour timeout")
    log.info(f"  Observability: X-Observe: true header")

    # Wait for shutdown — Windows-compatible (no add_signal_handler)
    shutdown_event = asyncio.Event()

    if PLATFORM != "windows":
        # POSIX: use native signal handlers in the event loop
        def _sig():
            log.info("Signal received, shutting down...")
            shutdown_event.set()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _sig)

    try:
        loop.run_until_complete(shutdown_event.wait())
    except KeyboardInterrupt:
        # On Windows, Ctrl+C raises KeyboardInterrupt in the main thread.
        # On POSIX, the signal handler sets shutdown_event before this fires.
        log.info("KeyboardInterrupt — shutting down...")
    finally:
        loop.run_until_complete(server.shutdown())
        loop.run_until_complete(runner.cleanup())
        loop.close()


if __name__ == "__main__":
    main()
