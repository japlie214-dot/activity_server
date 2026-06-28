# db/cloud.py
"""
Snowflake cloud database connection using the official Snowflake Python Connector.

Connects to a real Snowflake instance. If the connection fails during startup,
the server MUST shut down — no mock fallback.

Configuration is loaded from environment variables (see db/config.py):
  SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PRIVATE_KEY_PATH,
  SNOWFLAKE_WAREHOUSE, SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA

The interface matches what DualWriter and SchemaManager expect:
  execute(sql, params) → cursor with fetchall()/fetchone()
  commit()
  close_sync()
"""
import logging
from db.config import (
    SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PRIVATE_KEY_PATH,
    SNOWFLAKE_WAREHOUSE, SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA,
)

log = logging.getLogger("activity-server")


class CloudConnection:
    """Real Snowflake connection using the official Python Connector."""

    def __init__(self):
        self._conn = None

    def open_sync(self):
        """Connect to Snowflake. Raises on failure — no fallback."""
        import snowflake.connector
        from cryptography.hazmat.primitives import serialization

        if not SNOWFLAKE_ACCOUNT:
            raise RuntimeError(
                "SNOWFLAKE_ACCOUNT not configured. "
                "Set it in .env or environment variables.")
        if not SNOWFLAKE_USER:
            raise RuntimeError(
                "SNOWFLAKE_USER not configured.")
        if not SNOWFLAKE_PRIVATE_KEY_PATH:
            raise RuntimeError(
                "SNOWFLAKE_PRIVATE_KEY_PATH not configured. "
                "Path to the .p8 private key file is required.")

        # Load private key
        with open(SNOWFLAKE_PRIVATE_KEY_PATH, "rb") as key_file:
            p_key = serialization.load_pem_private_key(
                key_file.read(),
                password=None,
            )

        pkb = p_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        self._conn = snowflake.connector.connect(
            account=SNOWFLAKE_ACCOUNT,
            user=SNOWFLAKE_USER,
            private_key=pkb,
            warehouse=SNOWFLAKE_WAREHOUSE or None,
            database=SNOWFLAKE_DATABASE or None,
            schema=SNOWFLAKE_SCHEMA or None,
        )
        log.info(f"  Snowflake connected: {SNOWFLAKE_ACCOUNT} (user={SNOWFLAKE_USER})")

    def close_sync(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self):
        return self._conn

    def execute(self, sql, params=None):
        cur = self._conn.cursor()
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        return cur

    def commit(self):
        self._conn.commit()
