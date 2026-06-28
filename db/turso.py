# db/turso.py
"""
Turso operational database connection using pyturso.

Local dev: turso.connect("./data/operational.db")
Production: turso.sync.connect("./data/operational.db", remote_url="libsql://...", auth_token="...")

Enables WAL mode for MVCC support (required for BEGIN CONCURRENT transactions).
"""
import logging
import turso
import turso.sync
from db.config import TURSO_URL, TURSO_AUTH_TOKEN, TURSO_LOCAL_PATH, DATA_DIR

log = logging.getLogger("activity-server")


class TursoConnection:
    """Wraps a pyturso connection to Turso (local or remote-synced)."""

    def __init__(self):
        self._conn = None

    def open_sync(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if TURSO_URL:
            # Remote-synced: local file + remote Turso
            self._conn = turso.sync.connect(
                TURSO_LOCAL_PATH,
                remote_url=TURSO_URL,
                auth_token=TURSO_AUTH_TOKEN,
            )
            log.info(f"  Turso connected (synced): {TURSO_LOCAL_PATH} ↔ {TURSO_URL}")
        else:
            # Local only
            self._conn = turso.connect(TURSO_LOCAL_PATH)
            log.info(f"  Turso connected (local): {TURSO_LOCAL_PATH}")

        # Enable MVCC mode (required for BEGIN CONCURRENT transactions).
        # pyturso wraps the Turso Database Rust rewrite which supports MVCC.
        # PRAGMA journal_mode='mvcc' enables concurrent write transactions
        # with optimistic concurrency control and snapshot isolation.
        try:
            cur = self._conn.execute("PRAGMA journal_mode='mvcc'")
            mode = cur.fetchone()
            log.info(f"  Turso MVCC mode enabled (journal_mode={mode[0]})")
        except Exception as e:
            log.warning(f"  Could not enable MVCC mode: {e}")
            # Fallback to WAL mode
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                log.info("  Turso WAL mode enabled (fallback)")
            except Exception as e2:
                log.warning(f"  Could not enable WAL mode: {e2}")

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
