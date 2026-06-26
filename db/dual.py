# db/dual.py
"""
Dual Writer — every write goes to Operational (Turso), then Cloud (Snowflake)
for cloud-synced tables only. Non-synced tables are operational-only.

Cloud-synced tables automatically get a row_hash computed for content-addressable
sync verification. If Cloud write fails, the operation is queued for retry
with exponential backoff.
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
                asyncio.create_task(self._pending.put((table, data, row_id)))

        return row_id

    async def _retry_loop(self):
        while True:
            table, data, row_id = await self._pending.get()
            for attempt in range(CLOUD_RETRY_ATTEMPTS):
                try:
                    cloud_data = {**data, "id": row_id}
                    c_cols = ", ".join(cloud_data.keys())
                    c_ph = ", ".join(["?"] * len(cloud_data))
                    self.cloud.execute(
                        f"INSERT INTO {table} ({c_cols}) VALUES ({c_ph})",
                        list(cloud_data.values()))
                    self.cloud.commit()
                    log.info(f"Cloud retry succeeded: {table} id={row_id}")
                    break
                except Exception as e:
                    wait = CLOUD_RETRY_BASE_WAIT * (2 ** attempt)
                    log.warning(f"Cloud retry {attempt+1}/{CLOUD_RETRY_ATTEMPTS} failed: {e}")
                    await asyncio.sleep(wait)
            else:
                log.error(f"Cloud write permanently failed: {table} id={row_id}")
