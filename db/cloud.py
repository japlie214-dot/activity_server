# db/cloud.py
"""
Snowflake cloud database — mock connection using pyturso (local SQLite).

In production, replace this with the Snowflake Python Connector.
Same interface as TursoConnection so DualWriter works with both.
"""
import logging
import turso
from db.config import CLOUD_DB_PATH, DATA_DIR

log = logging.getLogger("activity-server")


class CloudConnection:
    """Mock Snowflake connection using local pyturso."""

    def __init__(self):
        self._conn = None

    def open_sync(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = turso.connect(CLOUD_DB_PATH)
        log.info(f"  Cloud (Snowflake mock) connected: {CLOUD_DB_PATH}")

    def close_sync(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self):
        return self._conn

    def execute(self, sql, params=None):
        if params:
            return self._conn.execute(sql, params)
        return self._conn.execute(sql)

    def commit(self):
        self._conn.commit()
