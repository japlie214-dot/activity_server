# db/dual.py
"""
Dual Writer — every write goes to Operational (Turso), then Cloud (Snowflake)
for cloud-synced tables only. Non-synced tables are operational-only.

Cloud-synced tables automatically get a row_hash computed for content-addressable
sync verification. If Cloud write fails, the operation is queued for retry
with exponential backoff.

Supports three write patterns:
  - write():  INSERT into operational (+ cloud for synced tables)
  - upsert(): INSERT OR REPLACE into operational (+ cloud for synced tables)
  - delete(): DELETE from operational (+ cloud for synced tables)
"""
import asyncio
import logging
from db.config import CLOUD_RETRY_ATTEMPTS, CLOUD_RETRY_BASE_WAIT
from db.schema import CLOUD_SYNC_TABLES, compute_row_hash

log = logging.getLogger("activity-server")


class DualWriter:
    """Write-through to Turso, then Cloud (for synced tables only)."""

    def __init__(self, turso_conn, cloud_conn):
        self.turso = turso_conn
        self.cloud = cloud_conn
        self._pending: asyncio.Queue = asyncio.Queue()
        self._retry_task: asyncio.Task = None

    async def start(self):
        self._retry_task = asyncio.create_task(self._retry_loop())

    async def stop(self):
        if self._retry_task:
            self._retry_task.cancel()
            try:
                await self._retry_task
            except asyncio.CancelledError:
                pass

    def write(self, table: str, data: dict) -> int:
        """Insert into operational. If table is cloud-synced, also write to cloud.

        For cloud-synced tables, automatically computes and stores row_hash.
        Returns the new row id from the operational database.
        """
        is_synced = table in CLOUD_SYNC_TABLES

        # Compute hash for synced tables
        if is_synced:
            data["row_hash"] = compute_row_hash(data)

        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        values = list(data.values())

        cur = self.turso.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", values)
        row_id = cur.lastrowid
        self.turso.commit()

        # Cloud (only for synced tables)
        if is_synced:
            try:
                cloud_data = {**data, "id": row_id}
                c_cols = ", ".join(cloud_data.keys())
                c_ph = ", ".join(["?"] * len(cloud_data))
                self.cloud.execute(
                    f"INSERT INTO {table} ({c_cols}) VALUES ({c_ph})",
                    list(cloud_data.values()))
                self.cloud.commit()
            except Exception as e:
                log.warning(f"Cloud write failed for {table}: {e} — queued")
                asyncio.create_task(self._pending.put(("write", table, data, row_id)))

        return row_id

    def upsert(self, table: str, data: dict, key_columns: list = None) -> None:
        """INSERT OR REPLACE into operational. If table is cloud-synced, also upsert to cloud.

        For cloud-synced tables, automatically computes and stores row_hash.
        Used by stock_financials and stock_notes which use composite primary keys.

        Args:
            table: Table name
            data: Column values dict
            key_columns: Not used for SQL (INSERT OR REPLACE uses PRIMARY KEY),
                         but kept for API clarity.
        """
        is_synced = table in CLOUD_SYNC_TABLES

        # Compute hash for synced tables
        if is_synced:
            data["row_hash"] = compute_row_hash(data)

        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        values = list(data.values())

        self.turso.execute(
            f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})", values)
        self.turso.commit()

        # Cloud (only for synced tables)
        if is_synced:
            try:
                self.cloud.execute(
                    f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})",
                    values)
                self.cloud.commit()
            except Exception as e:
                log.warning(f"Cloud upsert failed for {table}: {e} — queued")
                asyncio.create_task(self._pending.put(("upsert", table, data, None)))

    def delete(self, table: str, where: str, params: tuple = ()) -> int:
        """Delete rows from operational. If table is cloud-synced, also delete from cloud.

        Returns the number of rows deleted from operational.
        """
        is_synced = table in CLOUD_SYNC_TABLES

        cur = self.turso.execute(f"DELETE FROM {table} {where}", params)
        deleted = cur.rowcount
        self.turso.commit()

        # Cloud (only for synced tables)
        if is_synced:
            try:
                self.cloud.execute(f"DELETE FROM {table} {where}", params)
                self.cloud.commit()
            except Exception as e:
                log.warning(f"Cloud delete failed for {table}: {e} — queued")
                asyncio.create_task(self._pending.put(("delete", table, where, params)))

        return deleted

    def execute_on_both(self, sql: str, params: tuple = ()) -> None:
        """Execute raw SQL on both operational and cloud (for synced table operations).

        Use for complex operations like DELETE with subqueries.
        Caller is responsible for ensuring the SQL is safe for both databases.
        """
        self.turso.execute(sql, params)
        self.turso.commit()
        try:
            self.cloud.execute(sql, params)
            self.cloud.commit()
        except Exception as e:
            log.warning(f"Cloud execute failed: {e} — queued")
            asyncio.create_task(self._pending.put(("execute", sql, params, None)))

    async def _retry_loop(self):
        while True:
            op, table_or_sql, data_or_params, extra = await self._pending.get()
            for attempt in range(CLOUD_RETRY_ATTEMPTS):
                try:
                    if op == "write":
                        cloud_data = {**data_or_params, "id": extra}
                        c_cols = ", ".join(cloud_data.keys())
                        c_ph = ", ".join(["?"] * len(cloud_data))
                        self.cloud.execute(
                            f"INSERT INTO {table_or_sql} ({c_cols}) VALUES ({c_ph})",
                            list(cloud_data.values()))
                        self.cloud.commit()
                        log.info(f"Cloud retry succeeded: {op} {table_or_sql}")
                        break
                    elif op == "upsert":
                        cols = ", ".join(data_or_params.keys())
                        ph = ", ".join(["?"] * len(data_or_params))
                        self.cloud.execute(
                            f"INSERT OR REPLACE INTO {table_or_sql} ({cols}) VALUES ({ph})",
                            list(data_or_params.values()))
                        self.cloud.commit()
                        log.info(f"Cloud retry succeeded: {op} {table_or_sql}")
                        break
                    elif op == "delete":
                        self.cloud.execute(f"DELETE FROM {table_or_sql} {data_or_params}", extra)
                        self.cloud.commit()
                        log.info(f"Cloud retry succeeded: {op} {table_or_sql}")
                        break
                    elif op == "execute":
                        self.cloud.execute(table_or_sql, data_or_params)
                        self.cloud.commit()
                        log.info(f"Cloud retry succeeded: execute")
                        break
                except Exception as e:
                    wait = CLOUD_RETRY_BASE_WAIT * (2 ** attempt)
                    log.warning(f"Cloud retry {attempt+1}/{CLOUD_RETRY_ATTEMPTS} failed: {e}")
                    await asyncio.sleep(wait)
            else:
                log.error(f"Cloud write permanently failed: {op} {table_or_sql}")
