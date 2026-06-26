# server/lifecycle.py
"""
Server lifecycle — startup and shutdown orchestration.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from db.turso import TursoConnection
from db.cloud import CloudConnection
from db.dual import DualWriter
from db.schema import SchemaManager, OPERATIONAL_TABLES, CLOUD_SYNC_TABLES
from db.config import TELEMETRY_DB_PATH, ARTIFACTS_DIR, DATA_DIR
from server.telemetry import set_telemetry_queue

log = logging.getLogger("activity-server")


async def _telemetry_drain(tel_conn, queue):
    """Background: drain telemetry queue → telemetry DB."""
    while True:
        meta = await queue.get()
        try:
            now = datetime.now(timezone.utc).isoformat()
            started = datetime.fromtimestamp(
                meta.start_time, tz=timezone.utc).isoformat()
            tel_conn.execute(
                "INSERT INTO activity_log"
                " (activity_name, input_data, output_data, error, ok,"
                "  duration_ms, started_at, logged_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (meta.name, meta.input_data, meta.output_data,
                 meta.error, int(meta.ok), meta.duration_ms,
                 started, now))
            tel_conn.commit()
        except Exception as e:
            log.error(f"Telemetry write failed: {e}")


class Server:
    """Main server orchestrator. Owns all connections and lifecycle."""

    def __init__(self, resolve_source=None):
        self.resolve_source = resolve_source
        self.turso = TursoConnection()
        self.cloud = CloudConnection()
        self.tel_conn = None  # CloudConnection for telemetry (local file)
        self.schema_manager = None
        self.dual_writer = None
        self.tools_locked = True
        self.started_at = ""
        self.tool_registry = {}
        self._telemetry_task = None
        self._telemetry_queue = asyncio.Queue()

    def startup_sync(self):
        """Full startup sequence (synchronous — pyturso is sync)."""
        self.started_at = datetime.now(timezone.utc).isoformat()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        Path(ARTIFACTS_DIR).mkdir(parents=True, exist_ok=True)

        log.info("=" * 60)
        log.info("ACTIVITY SERVER — STARTUP")
        log.info("=" * 60)

        # 1. Open databases
        log.info("[1/7] Opening databases...")
        self.turso.open_sync()
        self.cloud.open_sync()
        self.tel_conn = CloudConnection()
        import turso as _turso
        self.tel_conn._conn = _turso.connect(TELEMETRY_DB_PATH)
        log.info(f"  Telemetry connected: {TELEMETRY_DB_PATH}")

        # 2. Schema validation & repair
        log.info("[2/7] Validating schemas...")
        self.schema_manager = SchemaManager(
            self.turso, self.cloud, self.tel_conn)
        self.schema_manager.validate_operational()
        self.schema_manager.validate_cloud()
        self.schema_manager.validate_telemetry()
        log.info("  Schema validation complete.")

        # 3. Sync check — only for cloud-synced tables
        log.info("[3/7] Checking synced tables ({})...".format(
            ", ".join(sorted(CLOUD_SYNC_TABLES))))
        sync = self.schema_manager.check_sync()
        for tbl, info in sync.items():
            s = "✓" if info["match"] else "✗"
            log.info(f"  {s} {tbl}: op={info['op_count']} cloud={info['cloud_count']} "
                     f"hash_mismatches={info['hash_mismatches']}")

        # 4. Handle out-of-sync
        if not self.schema_manager.in_sync:
            if self.resolve_source:
                log.warning(f"  Out of sync — auto-resolving with {self.resolve_source}")
                self.resolve_sync(self.resolve_source)
            else:
                log.warning("  ⚠ SYNCED TABLES OUT OF SYNC — DEGRADED MODE")
                log.warning("  Server is running but tools are LOCKED.")
                log.warning("  Resolve by POST /resolve {\"source\":\"operational\"} "
                            "or {\"source\":\"cloud\"}")
                self.tools_locked = True
        else:
            self.tools_locked = False
            log.info("  Synced tables in sync. ✓")

        # 5. Set telemetry queue
        set_telemetry_queue(self._telemetry_queue)

    async def post_startup(self):
        """Async post-startup: register tools, start background tasks."""
        from tools import TOOL_REGISTRY
        self.tool_registry = TOOL_REGISTRY

        log.info("[4/7] Registering tools...")
        self._register_tools()

        log.info("[5/7] Starting background tasks...")
        self.dual_writer = DualWriter(self.turso, self.cloud)
        await self.dual_writer.start()
        self._telemetry_task = asyncio.create_task(
            _telemetry_drain(self.tel_conn, self._telemetry_queue))

        log.info("[6/7] Tool inventory:")
        for name, tool in self.tool_registry.items():
            log.info(f"  • {name}: {tool.description[:60]}")
        log.info(f"[7/7] {len(self.tool_registry)} tools registered.")

        log.info("=" * 60)
        log.info(f"Server ready. Tools {'LOCKED' if self.tools_locked else 'ACTIVE'}.")
        log.info("=" * 60)

    def _register_tools(self):
        now = datetime.now(timezone.utc).isoformat()
        for name, tool in self.tool_registry.items():
            schema_json = json.dumps(tool.input_schema)
            self.turso.execute(
                "INSERT OR REPLACE INTO tools"
                " (name, description, input_schema, registered_at)"
                " VALUES (?, ?, ?, ?)",
                (name, tool.description, schema_json, now))
        self.turso.commit()

    def resolve_sync(self, source):
        """Resolve sync conflict by copying all data from source to destination.

        Only affects cloud-synced tables. After resolution, tools are unlocked.
        """
        src = self.turso if source == "operational" else self.cloud
        dst = self.cloud if source == "operational" else self.turso
        for tbl_name in CLOUD_SYNC_TABLES:
            expected = OPERATIONAL_TABLES[tbl_name]
            src_cols = self.schema_manager._table_columns(src, tbl_name)
            dst_cols = self.schema_manager._table_columns(dst, tbl_name)
            common = [c for c in src_cols if c in dst_cols]
            if not common:
                continue
            col_list = ", ".join(common)
            cur = src.execute(f"SELECT {col_list} FROM {tbl_name}")
            rows = cur.fetchall()
            dst.execute(f"DELETE FROM {tbl_name}")
            for row in rows:
                ph = ", ".join(["?"] * len(row))
                dst.execute(
                    f"INSERT INTO {tbl_name} ({col_list}) VALUES ({ph})", list(row))
            dst.commit()
        self.tools_locked = False
        log.info(f"  Resolved using {source}. Tools unlocked.")

    async def shutdown(self):
        log.info("=" * 60)
        log.info("ACTIVITY SERVER — SHUTDOWN")
        log.info("=" * 60)

        if self.dual_writer:
            await self.dual_writer.stop()
        if self._telemetry_task:
            self._telemetry_task.cancel()
            try:
                await self._telemetry_task
            except asyncio.CancelledError:
                pass

        # Drain remaining telemetry
        while not self._telemetry_queue.empty():
            meta = self._telemetry_queue.get_nowait()
            try:
                now = datetime.now(timezone.utc).isoformat()
                started = datetime.fromtimestamp(
                    meta.start_time, tz=timezone.utc).isoformat()
                self.tel_conn.execute(
                    "INSERT INTO activity_log"
                    " (activity_name, input_data, output_data, error, ok,"
                    "  duration_ms, started_at, logged_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (meta.name, meta.input_data, meta.output_data,
                     meta.error, int(meta.ok), meta.duration_ms, started, now))
            except Exception:
                pass
        self.tel_conn.commit()

        # Final sync — operational wins for synced tables
        log.info("Final sync check (operational wins)...")
        sync = self.schema_manager.check_sync()
        if not self.schema_manager.in_sync:
            log.warning("  Out of sync — forcing operational → cloud")
            self.resolve_sync("operational")

        self.tel_conn.close_sync()
        self.cloud.close_sync()
        self.turso.close_sync()
        log.info("Shutdown complete.")



