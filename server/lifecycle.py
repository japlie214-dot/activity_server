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
        self.tel_conn = None  # telemetry connection (local Turso)
        self.schema_manager = None
        self.dual_writer = None
        self.tools_locked = True
        self.started_at = ""
        self.tool_registry = {}
        self._telemetry_task = None
        self._telemetry_queue = asyncio.Queue()

    def startup_sync(self):
        """Full startup sequence (synchronous).

        8-step startup:
          [1/8] Opening Snowflake connection (fail-hard if unavailable)
          [2/8] Opening operational database
          [3/8] Opening telemetry database
          [4/8] Validating Operational schemas
          [5/8] Validating Cloud schemas
          [6/8] Validating Telemetry schema
          [7/8] Checking synced tables
          [8/8] Setting telemetry queue

        Golden rules:
          - Snowflake connection failure → shutdown (no mock fallback)
          - Auto-resolve only when one side is truly empty (0 rows)
          - Hash mismatches with data on both sides → tools LOCKED
        """
        self.started_at = datetime.now(timezone.utc).isoformat()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        Path(ARTIFACTS_DIR).mkdir(parents=True, exist_ok=True)

        log.info("=" * 60)
        log.info("ACTIVITY SERVER — STARTUP")
        log.info("=" * 60)

        # [1/8] Open Snowflake — fail-hard if unavailable
        log.info("[1/8] Opening Snowflake connection...")
        try:
            self.cloud.open_sync()
        except Exception as e:
            log.error(f"  FATAL: Snowflake connection failed: {e}")
            log.error("  Server cannot start without cloud connection.")
            raise SystemExit(1) from e

        self.schema_manager = SchemaManager(
            self.turso, self.cloud, None)  # tel_conn set later
        # Create missing sync tables in cloud first
        for tbl_name in CLOUD_SYNC_TABLES:
            expected = OPERATIONAL_TABLES.get(tbl_name)
            if expected and not self.schema_manager._table_exists(self.cloud, tbl_name):
                self.schema_manager._create_table(self.cloud, tbl_name, expected)
        log.info(f"  All {len(CLOUD_SYNC_TABLES)} sync tables verified in Snowflake. ✓")

        # [2/8] Opening operational database...
        log.info("[2/8] Opening operational database...")
        self.turso.open_sync()

        # [3/8] Opening telemetry database...
        log.info("[3/8] Opening telemetry database...")
        self.tel_conn = CloudConnection()
        import turso as _turso
        self.tel_conn._conn = _turso.connect(TELEMETRY_DB_PATH)
        log.info(f"  Telemetry connected: {TELEMETRY_DB_PATH}")
        self.schema_manager.tel = self.tel_conn

        # [4/8] Validating Operational schemas FIRST
        log.info("[4/8] Validating Operational schemas...")
        self.schema_manager.validate_operational()
        log.info("  Operational schema validation complete.")

        # [5/8] Validating Cloud schemas SECOND
        log.info("[5/8] Validating Cloud schemas...")
        self.schema_manager.validate_cloud()
        log.info("  Cloud schema validation complete.")

        # [6/8] Validating Telemetry schema
        log.info("[6/8] Validating Telemetry schema...")
        self.schema_manager.validate_telemetry()
        log.info("  Telemetry schema validation complete.")

        # [7/8] Checking synced tables
        log.info("[7/8] Checking synced tables ({})...".format(
            ", ".join(sorted(CLOUD_SYNC_TABLES))))
        sync = self.schema_manager.check_sync()
        for tbl, info in sync.items():
            s = "✓" if info["match"] else "✗"
            log.info(f"  {s} {tbl}: op={info['op_count']} cloud={info['cloud_count']} "
                     f"hash_mismatches={info['hash_mismatches']}")

        # Handle out-of-sync — golden rules:
        #   - Auto-resolve ONLY when one side is truly empty (0 rows)
        #   - Both sides have data but mismatched → tools LOCKED
        if not self.schema_manager.in_sync:
            # Build detailed out-of-sync report
            out_of_sync = {tbl: info for tbl, info in sync.items() if not info["match"]}
            empty_tables = {tbl: info for tbl, info in out_of_sync.items()
                           if info["op_count"] == 0 or info["cloud_count"] == 0}
            mismatch_tables = {tbl: info for tbl, info in out_of_sync.items()
                               if tbl not in empty_tables}

            if empty_tables:
                empty_names = ", ".join(sorted(empty_tables.keys()))
                log.warning(f"  Tables with empty side: {empty_names}")
            if mismatch_tables:
                mismatch_detail = ", ".join(
                    f"{tbl}(op={info['op_count']},cloud={info['cloud_count']},mismatches={info['hash_mismatches']})"
                    for tbl, info in sorted(mismatch_tables.items()))
                log.warning(f"  Tables with hash mismatches: {mismatch_detail}")

            can_auto_resolve = self._can_auto_resolve(sync)
            if can_auto_resolve and self.resolve_source:
                log.warning(f"  Auto-resolving with {self.resolve_source} (tables: {empty_names})")
                self.resolve_sync(self.resolve_source)
            elif can_auto_resolve:
                empty_side = self._find_empty_side(sync)
                auto_source = "cloud" if empty_side == "operational" else "operational"
                log.warning(f"  Auto-resolving with {auto_source} (tables: {empty_names})")
                self.resolve_sync(auto_source)
            else:
                log.error("  ⚠ SYNCED TABLES OUT OF SYNC — DATA MISMATCH")
                log.error("  Both operational and cloud have data but row hashes differ.")
                log.error(f"  Affected tables: {', '.join(sorted(mismatch_tables.keys()))}")
                log.error("  Server is running but tools are LOCKED.")
                log.error("  Resolve manually: POST /resolve {\"source\":\"operational\"} "
                            "or {\"source\":\"cloud\"}")
                self.tools_locked = True
        else:
            self.tools_locked = False
            log.info("  Synced tables in sync. ✓")

        # [8/8] Set telemetry queue
        log.info("[8/8] Setting telemetry queue...")
        set_telemetry_queue(self._telemetry_queue)

    @staticmethod
    def _can_auto_resolve(sync: dict) -> bool:
        """Auto-resolve is allowed only when at least one table has one side empty.

        Returns True if ANY synced table has op_count==0 or cloud_count==0.
        Returns False if all out-of-sync tables have data on BOTH sides.
        """
        for tbl, info in sync.items():
            if not info["match"] and (info["op_count"] == 0 or info["cloud_count"] == 0):
                return True
        return False

    @staticmethod
    def _find_empty_side(sync: dict) -> str:
        """Determine which side is empty for auto-resolve.

        Returns 'operational' if operational is empty, 'cloud' otherwise.
        """
        for tbl, info in sync.items():
            if not info["match"] and info["op_count"] == 0:
                return "operational"
        return "cloud"

    async def post_startup(self):
        """Async post-startup: register tools, start background tasks."""
        from tools import TOOL_REGISTRY
        self.tool_registry = TOOL_REGISTRY

        log.info("[post] Registering tools...")
        self._register_tools()

        log.info("[post] Starting background tasks...")
        self.dual_writer = DualWriter(self.turso, self.cloud)
        await self.dual_writer.start()
        self._telemetry_task = asyncio.create_task(
            _telemetry_drain(self.tel_conn, self._telemetry_queue))

        log.info("[post] Tool inventory:")
        for name, tool in self.tool_registry.items():
            log.info(f"  • {name}: {tool.description[:60]}")
        log.info(f"[post] {len(self.tool_registry)} tools registered.")

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

    def assert_dual_write(self, table: str, caller: str = ""):
        """Guard: raise if a synced table is being written outside DualWriter.

        Tools should call this before any raw conn.execute() on user-data tables.
        """
        if self.dual_writer and self.dual_writer.is_synced_table(table):
            raise RuntimeError(
                f"ILLEGAL WRITE: '{table}' is cloud-synced. "
                f"Use DualWriter.write/upsert/delete. Caller: {caller or 'unknown'}")

    def resolve_sync(self, source):
        """Resolve sync conflict by copying all data from source to destination.

        Only affects cloud-synced tables. After resolution, tools are unlocked.
        Uses batched inserts for performance on Snowflake.
        """
        from db.schema import SchemaManager

        src = self.turso if source == "operational" else self.cloud
        dst = self.cloud if source == "operational" else self.turso
        ph = "%s" if SchemaManager._is_snowflake(dst) else "?"
        is_sf = SchemaManager._is_snowflake(dst)

        for tbl_name in CLOUD_SYNC_TABLES:
            src_cols = self.schema_manager._table_columns(src, tbl_name)
            dst_cols = self.schema_manager._table_columns(dst, tbl_name)
            common = [c for c in src_cols if c in dst_cols]
            if not common:
                continue
            col_list = ", ".join(common)
            cur = src.execute(f"SELECT {col_list} FROM {tbl_name}")
            rows = cur.fetchall()
            if not rows:
                continue

            dst.execute(f"DELETE FROM {tbl_name}")
            dst.commit()

            # Batch insert — 100 rows at a time for Snowflake performance
            batch_size = 100 if is_sf else 500
            placeholders = ", ".join([ph] * len(common))
            insert_sql = f"INSERT INTO {tbl_name} ({col_list}) VALUES ({placeholders})"
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i + batch_size]
                for row in batch:
                    dst.execute(insert_sql, list(row))
                dst.commit()
            log.info(f"    [{source}] Synced {tbl_name}: {len(rows)} rows")

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

        # No sync check on shutdown — sync is only verified on startup.
        # Forcing operational-to-cloud on shutdown could overwrite healthy
        # cloud data if the operational DB had issues during the run.

        self.tel_conn.close_sync()
        self.cloud.close_sync()
        self.turso.close_sync()
        log.info("Shutdown complete.")



