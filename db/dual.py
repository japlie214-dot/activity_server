# db/dual.py
"""
Dual Writer — every write goes to Operational (Turso), then Cloud (Snowflake)
for cloud-synced tables only. Non-synced tables are operational-only.

Cloud-synced tables automatically get a row_hash computed for content-addressable
sync verification. If Cloud write fails, the operation is queued for retry
with exponential backoff.

All write transactions on Turso use BEGIN CONCURRENT for maximum concurrency
via MVCC (Multi-Version Concurrency Control).

See: https://docs.turso.tech/sdk/python/quickstart
See: Turso Database Manual — Transactions section
"""
import asyncio
import logging
from db.config import CLOUD_RETRY_ATTEMPTS, CLOUD_RETRY_BASE_WAIT
from db.schema import CLOUD_SYNC_TABLES, compute_row_hash

log = logging.getLogger("activity-server")

# Natural key columns for each synced table (used for Snowflake upserts).
# These are the columns that form the UNIQUE constraint in SQLite —
# INSERT OR REPLACE triggers on violation of these.
_UPSERT_KEYS = {
    "sf_quarterly_facts": ["ticker", "statement_type", "concept", "quarter"],
    "sf_tickers":         ["ticker"],
    "sn_filings":         ["filing_id"],
    "sn_notes":           ["note_id"],
    "sn_note_details":    ["detail_id"],
    "sn_detail_registry": ["registry_id"],
    "artifacts":          ["id"],
}


def _begin_concurrent(conn):
    """Start a CONCURRENT transaction on the Turso connection."""
    try:
        conn.execute("BEGIN CONCURRENT")
    except Exception:
        conn.execute("BEGIN IMMEDIATE")


def _commit(conn):
    conn.execute("COMMIT")


def _rollback(conn):
    try:
        conn.execute("ROLLBACK")
    except Exception:
        pass


def _is_snowflake(conn) -> bool:
    """Detect if connection is Snowflake (vs Turso/SQLite)."""
    return hasattr(conn, '_conn') and hasattr(conn._conn, 'rest')


def _ph(conn) -> str:
    """Return the parameter placeholder: %s for Snowflake, ? for SQLite."""
    return "%s" if _is_snowflake(conn) else "?"


def _insert(conn, table: str, data: dict) -> None:
    """INSERT a row using the correct placeholder for the connection type."""
    ph = _ph(conn)
    cols = ", ".join(data.keys())
    placeholders = ", ".join([ph] * len(data))
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", list(data.values()))


def _insert_or_replace(conn, table: str, data: dict) -> None:
    """Upsert using the correct SQL for the connection type.

    - SQLite (Turso): INSERT OR REPLACE (triggers on UNIQUE constraint violation)
    - Snowflake: DELETE by natural key + INSERT (equivalent behavior)
    """
    if _is_snowflake(conn):
        ph = "%s"
        key_cols = _UPSERT_KEYS.get(table, [])
        if not key_cols:
            # Fallback: just insert
            cols = ", ".join(data.keys())
            placeholders = ", ".join([ph] * len(data))
            conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", list(data.values()))
            return

        # Build DELETE WHERE clause from natural key columns
        conditions = []
        params = []
        for k in key_cols:
            if k in data:
                conditions.append(f"{k.upper()} = {ph}")
                params.append(data[k])
        if conditions:
            conn.execute(f"DELETE FROM {table} WHERE {' AND '.join(conditions)}", params)

        # INSERT the new row
        cols = ", ".join(data.keys())
        placeholders = ", ".join([ph] * len(data))
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", list(data.values()))
    else:
        ph = "?"
        cols = ", ".join(data.keys())
        placeholders = ", ".join([ph] * len(data))
        conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})", list(data.values()))


class DualWriter:
    """Write-through to Turso, then Cloud (for synced tables only).

    All writes to cloud-synced tables MUST go through this class.
    Direct conn.execute() on synced tables is a bug — it bypasses
    cloud replication and row_hash computation.
    """

    def __init__(self, turso_conn, cloud_conn):
        self.turso = turso_conn
        self.cloud = cloud_conn
        self._pending: asyncio.Queue = asyncio.Queue()
        self._retry_task: asyncio.Task = None
        self._enforce_mode = True  # When True, block raw writes to synced tables

    @staticmethod
    def is_synced_table(table: str) -> bool:
        """Check if a table requires DualWriter."""
        return table in CLOUD_SYNC_TABLES

    def assert_not_synced(self, table: str, caller: str = ""):
        """Raise if someone tries to write to a synced table outside DualWriter.

        Call this from conn.execute() wrappers or guards to catch violations.
        """
        if self._enforce_mode and self.is_synced_table(table):
            raise RuntimeError(
                f"ILLEGAL WRITE: '{table}' is a cloud-synced table. "
                f"All writes must go through DualWriter (write/upsert/delete). "
                f"Caller: {caller or 'unknown'}")

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

        Uses BEGIN CONCURRENT for Turso (MVCC) to allow concurrent writes.
        """
        is_synced = table in CLOUD_SYNC_TABLES

        if is_synced:
            data["row_hash"] = compute_row_hash(data)

        ph_op = _ph(self.turso)
        cols = ", ".join(data.keys())
        placeholders = ", ".join([ph_op] * len(data))
        values = list(data.values())

        _begin_concurrent(self.turso)
        try:
            cur = self.turso.execute(
                f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", values)
            row_id = cur.lastrowid
            _commit(self.turso)
        except Exception:
            _rollback(self.turso)
            raise

        if is_synced:
            try:
                cloud_data = {**data, "id": row_id}
                _insert(self.cloud, table, cloud_data)
                self.cloud.commit()
            except Exception as e:
                log.warning(f"Cloud write failed for {table}: {e} — queued")
                asyncio.create_task(self._pending.put(("write", table, data, row_id)))

        return row_id

    def upsert(self, table: str, data: dict, key_columns: list = None) -> None:
        """INSERT OR REPLACE into operational. If table is cloud-synced, also upsert to cloud.

        For cloud-synced tables, automatically computes and stores row_hash.
        Used by stock_financials and stock_notes which use composite primary keys.

        Uses BEGIN CONCURRENT for Turso (MVCC) to allow concurrent writes.
        """
        is_synced = table in CLOUD_SYNC_TABLES

        if is_synced:
            data["row_hash"] = compute_row_hash(data)

        _begin_concurrent(self.turso)
        try:
            _insert_or_replace(self.turso, table, data)
            _commit(self.turso)
        except Exception:
            _rollback(self.turso)
            raise

        if is_synced:
            try:
                _insert_or_replace(self.cloud, table, data)
                self.cloud.commit()
            except Exception as e:
                log.warning(f"Cloud upsert failed for {table}: {e} — queued")
                asyncio.create_task(self._pending.put(("upsert", table, data, None)))

    def delete(self, table: str, where: str, params: tuple = ()) -> int:
        """Delete rows from operational. If table is cloud-synced, also delete from cloud.

        Returns the number of rows deleted from operational.

        Note: WHERE clause must use '?' placeholders for SQLite (operational)
        and '%s' for Snowflake (cloud). This method handles the conversion.
        """
        is_synced = table in CLOUD_SYNC_TABLES

        _begin_concurrent(self.turso)
        try:
            cur = self.turso.execute(f"DELETE FROM {table} {where}", params)
            deleted = cur.rowcount
            _commit(self.turso)
        except Exception:
            _rollback(self.turso)
            raise

        if is_synced:
            try:
                # Convert ? placeholders to %s for Snowflake
                cloud_where = where.replace("?", "%s") if _is_snowflake(self.cloud) else where
                self.cloud.execute(f"DELETE FROM {table} {cloud_where}", params)
                self.cloud.commit()
            except Exception as e:
                log.warning(f"Cloud delete failed for {table}: {e} — queued")
                asyncio.create_task(self._pending.put(("delete", table, where, params)))

        return deleted

    def execute_on_both(self, sql: str, params: tuple = ()) -> None:
        """Execute raw SQL on both operational and cloud (for synced table operations).

        Use for complex operations like DELETE with subqueries.
        Caller is responsible for ensuring the SQL is safe for both databases.
        Note: SQL must use '?' placeholders. This method converts to '%s' for Snowflake.
        """
        _begin_concurrent(self.turso)
        try:
            self.turso.execute(sql, params)
            _commit(self.turso)
        except Exception:
            _rollback(self.turso)
            raise
        try:
            cloud_sql = sql.replace("?", "%s") if _is_snowflake(self.cloud) else sql
            self.cloud.execute(cloud_sql, params)
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
                        _insert(self.cloud, table_or_sql, cloud_data)
                        self.cloud.commit()
                        log.info(f"Cloud retry succeeded: {op} {table_or_sql}")
                        break
                    elif op == "upsert":
                        _insert_or_replace(self.cloud, table_or_sql, data_or_params)
                        self.cloud.commit()
                        log.info(f"Cloud retry succeeded: {op} {table_or_sql}")
                        break
                    elif op == "delete":
                        cloud_where = data_or_params.replace("?", "%s") if _is_snowflake(self.cloud) else data_or_params
                        self.cloud.execute(f"DELETE FROM {table_or_sql} {cloud_where}", extra)
                        self.cloud.commit()
                        log.info(f"Cloud retry succeeded: {op} {table_or_sql}")
                        break
                    elif op == "execute":
                        self.cloud.execute(table_or_sql, data_or_params)
                        self.cloud.commit()
                        log.info("Cloud retry succeeded: execute")
                        break
                except Exception as e:
                    wait = CLOUD_RETRY_BASE_WAIT * (2 ** attempt)
                    log.warning(f"Cloud retry {attempt+1}/{CLOUD_RETRY_ATTEMPTS} failed: {e}")
                    await asyncio.sleep(wait)
            else:
                log.error(f"Cloud write permanently failed: {op} {table_or_sql}")
