# db/schema.py
"""
Expected Schema definitions + SchemaManager for auto-heal.

The Expected Schema is the single source of truth for what the databases SHOULD look like.
On startup, SchemaManager compares actual state against expected and auto-repairs:
  - Missing tables → create
  - Missing columns → add with defaults (computed if needed)
  - Unexpected columns → backup table, drop, recreate, repopulate, compute new columns
  - Unexpected tables → drop (operational) / leave alone (cloud)

MIGRATION SCRIPTS ARE ILLEGAL. The auto-heal mechanism replaces them entirely.
See db/README.md for rationale.

## Schema Design

### Cloud-Synced Tables (CLOUD_SYNC_TABLES)
These tables replicate to Snowflake. Each has a `row_hash` column for
content-addressable sync — comparing hashes is O(n) instead of comparing
every column. The hash is computed from all columns except `id` and `row_hash`.

- **artifacts** — File artifacts produced by tools. The actual user-created
  content. Losing these means losing work.

### Operational-Only Tables
These live only in Turso. Rebuilt on startup or from tool execution.

- **tools** — Tool registry. Rebuilt every startup from discovered tools.
- **tool_runs** — Execution history. Auto-recorded by the handler for every
  tool invocation. Provides "what did I run and what happened" without
  requiring X-Observe.

### Telemetry Tables (local telemetry.db)
- **activity_log** — High-volume Activity telemetry. Drained from an async
  queue. Local only — never synced.
"""
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Union

log = logging.getLogger("activity-server")


@dataclass
class Col:
    name: str
    type: str
    default: str = "NULL"
    # If set, called as compute(row_data: dict) -> value to fill this column
    # when repopulating from a backup that lacks it. For static defaults,
    # leave as None — the SQL DEFAULT handles it.
    compute: Optional[Callable[[dict], Any]] = None


@dataclass
class TableSchema:
    name: str
    columns: List[Col] = field(default_factory=list)
    def col_names(self) -> set:
        return {c.name for c in self.columns}
    def get_col(self, name: str) -> Optional[Col]:
        for c in self.columns:
            if c.name == name:
                return c
        return None


def _compute_row_hash(row_data: dict) -> str:
    """Compute a SHA-256 hash of row data for sync verification.

    Excludes 'id' and 'row_hash'. Column values are sorted alphabetically
    by name and concatenated as "name=value;" pairs.
    """
    exclude = {"id", "row_hash"}
    pairs = []
    for key in sorted(row_data.keys()):
        if key in exclude:
            continue
        pairs.append(f"{key}={row_data[key]}")
    content = ";".join(pairs)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ── Cloud-synced tables ──────────────────────────────────────────────
#
# These tables replicate to Snowflake. The DualWriter automatically computes
# and stores a row_hash on every write. The SchemaManager uses these hashes
# to verify sync status without comparing every column.

CLOUD_SYNC_TABLES: Set[str] = {
    "artifacts",
    # stock_financials
    "sf_tickers",
    "sf_quarterly_facts",
    # stock_notes
    "sn_filings",
    "sn_notes",
    "sn_detail_registry",
    "sn_note_details",
}

# ── Operational schema (what Turso should look like) ─────────────────

OPERATIONAL_TABLES: Dict[str, TableSchema] = {
    "artifacts": TableSchema("artifacts", [
        Col("id",           "INTEGER PRIMARY KEY", "0"),
        Col("tool_name",    "TEXT",                "''"),
        Col("filename",     "TEXT",                "''"),
        Col("filepath",     "TEXT",                "''"),
        Col("size_bytes",   "INTEGER",             "0"),
        Col("content_preview","TEXT",              "''"),
        Col("created_at",   "TEXT",                "''"),
        Col("row_hash",     "TEXT",                "''",
            compute=_compute_row_hash),
    ]),
    "tool_runs": TableSchema("tool_runs", [
        Col("id",           "INTEGER PRIMARY KEY", "0"),
        Col("tool_name",    "TEXT",                "''"),
        Col("arguments_json","TEXT",               "'{}'"),
        Col("result_json",  "TEXT",                "'{}'"),
        Col("ok",           "INTEGER",             "1"),
        Col("error",        "TEXT",                "''"),
        Col("duration_ms",  "REAL",                "0.0"),
        Col("started_at",   "TEXT",                "''"),
        Col("completed_at", "TEXT",                "''"),
    ]),
    "tools": TableSchema("tools", [
        Col("id",           "INTEGER PRIMARY KEY", "0"),
        Col("name",         "TEXT UNIQUE",         "''"),
        Col("description",  "TEXT",                "''"),
        Col("input_schema", "TEXT",                "'{}'"),
        Col("registered_at","TEXT",                "''"),
    ]),

    # ── stock_financials tables ──
    "sf_tickers": TableSchema("sf_tickers", [
        Col("ticker",       "TEXT PRIMARY KEY",    "''"),
        Col("company_name", "TEXT",                "''"),
        Col("cik",          "TEXT",                "''"),
        Col("created_at",   "TEXT",                "''"),
        Col("updated_at",   "TEXT",                "''"),
        Col("row_hash",     "TEXT",                "''",
            compute=_compute_row_hash),
    ]),
    "sf_quarterly_facts": TableSchema("sf_quarterly_facts", [
        Col("id",             "INTEGER PRIMARY KEY", "0"),
        Col("ticker",        "TEXT",    "''"),
        Col("statement_type","TEXT",    "''"),
        Col("concept",       "TEXT",    "''"),
        Col("label",         "TEXT",    "''"),
        Col("quarter",       "TEXT",    "''"),
        Col("period_end",    "TEXT",    "''"),
        Col("fiscal_period", "TEXT",    "''"),
        Col("fiscal_year",   "INTEGER", "0"),
        Col("numeric_value", "REAL",    "NULL"),
        Col("unit",          "TEXT",    "'USD'"),
        Col("period_type",   "TEXT",    "''"),
        Col("depth",         "INTEGER", "0"),
        Col("is_total",      "INTEGER", "0"),
        Col("concept_order", "INTEGER", "0"),
        Col("content_hash",  "TEXT",    "''"),
        Col("extracted_at",  "TEXT",    "''"),
        Col("created_at",    "TEXT",    "''"),
        Col("updated_at",    "TEXT",    "''"),
        Col("row_hash",      "TEXT",    "''",
            compute=_compute_row_hash),
    ]),

    # ── stock_notes tables ──
    "sn_filings": TableSchema("sn_filings", [
        Col("filing_id",            "TEXT PRIMARY KEY", "''"),
        Col("ticker",               "TEXT",             "''"),
        Col("form",                 "TEXT",             "''"),
        Col("filing_date",          "TEXT",             "''"),
        Col("accession_no",         "TEXT",             "''"),
        Col("period_of_report",     "TEXT",             "''"),
        Col("company_name",         "TEXT",             "''"),
        Col("cik",                  "TEXT",             "''"),
        Col("fiscal_year_end_month","INTEGER",          "12"),
        Col("quarter",              "INTEGER",          "0"),
        Col("year",                 "INTEGER",          "0"),
        Col("content_hash",         "TEXT",             "''"),
        Col("updated_at",           "TEXT",             "''"),
        Col("row_hash",             "TEXT",             "''",
            compute=_compute_row_hash),
    ]),
    "sn_notes": TableSchema("sn_notes", [
        Col("note_id",              "TEXT PRIMARY KEY", "''"),
        Col("filing_id",            "TEXT",             "''"),
        Col("ticker",               "TEXT",             "''"),
        Col("form",                 "TEXT",             "''"),
        Col("accession_no",         "TEXT",             "''"),
        Col("note_number",          "INTEGER",          "0"),
        Col("title",                "TEXT",             "''"),
        Col("short_name",           "TEXT",             "''"),
        Col("narrative_text",       "TEXT",             "''"),
        Col("narrative_hash",       "TEXT",             "''"),
        Col("expands",              "TEXT",             "'[]'"),
        Col("expands_statements",   "TEXT",             "'[]'"),
        Col("table_count",          "INTEGER",          "0"),
        Col("details_count",        "INTEGER",          "0"),
        Col("quarter",              "INTEGER",          "0"),
        Col("year",                 "INTEGER",          "0"),
        Col("quarterly_status",     "TEXT",             "''"),
        Col("version",              "INTEGER",          "1"),
        Col("content_hash",         "TEXT",             "''"),
        Col("updated_at",           "TEXT",             "''"),
        Col("row_hash",             "TEXT",             "''",
            compute=_compute_row_hash),
    ]),
    "sn_detail_registry": TableSchema("sn_detail_registry", [
        Col("registry_id",            "TEXT PRIMARY KEY", "''"),
        Col("ticker",                 "TEXT",             "''"),
        Col("detail_table_name",      "TEXT",             "''"),
        Col("source_title",           "TEXT",             "''"),
        Col("source_note_number",     "INTEGER",          "0"),
        Col("source_accession_no",    "TEXT",             "''"),
        Col("role_or_type",           "TEXT",             "''"),
        Col("available_concepts",     "TEXT",             "'[]'"),
        Col("tidy_schema_version",    "INTEGER",          "1"),
        Col("row_count",              "INTEGER",          "0"),
        Col("quarter",                "INTEGER",          "0"),
        Col("year",                   "INTEGER",          "0"),
        Col("quarterly_status",       "TEXT",             "''"),
        Col("content_hash",           "TEXT",             "''"),
        Col("created_at",             "TEXT",             "''"),
        Col("updated_at",             "TEXT",             "''"),
        Col("row_hash",               "TEXT",             "''",
            compute=_compute_row_hash),
    ]),
    "sn_note_details": TableSchema("sn_note_details", [
        Col("detail_id",              "TEXT PRIMARY KEY", "''"),
        Col("accession_no",           "TEXT",             "''"),
        Col("note_number",            "INTEGER",          "0"),
        Col("detail_index",           "INTEGER",          "0"),
        Col("ticker",                 "TEXT",             "''"),
        Col("form",                   "TEXT",             "''"),
        Col("concept",                "TEXT",             "''"),
        Col("label",                  "TEXT",             "''"),
        Col("standard_concept",       "TEXT",             "''"),
        Col("level",                  "INTEGER",          "0"),
        Col("abstract",               "TEXT",             "''"),
        Col("dimension",              "TEXT",             "''"),
        Col("is_breakdown",           "TEXT",             "''"),
        Col("dimension_axis",         "TEXT",             "''"),
        Col("dimension_member",       "TEXT",             "''"),
        Col("dimension_member_label", "TEXT",             "''"),
        Col("dimension_label",        "TEXT",             "''"),
        Col("balance",                "TEXT",             "''"),
        Col("weight",                 "TEXT",             "''"),
        Col("preferred_sign",         "TEXT",             "''"),
        Col("parent_concept",         "TEXT",             "''"),
        Col("parent_abstract_concept","TEXT",             "''"),
        Col("period_raw",             "TEXT",             "''"),
        Col("period_end_date",        "TEXT",             "''"),
        Col("period_type",            "TEXT",             "''"),
        Col("value",                  "TEXT",             "''"),
        Col("row_order",              "INTEGER",          "0"),
        Col("content_hash",           "TEXT",             "''"),
        Col("extracted_at",           "TEXT",             "''"),
        Col("created_at",             "TEXT",             "''"),
        Col("updated_at",             "TEXT",             "''"),
        Col("row_hash",               "TEXT",             "''",
            compute=_compute_row_hash),
    ]),
}

# ── Telemetry schema (local only) ────────────────────────────────────

TELEMETRY_TABLES: Dict[str, TableSchema] = {
    "activity_log": TableSchema("activity_log", [
        Col("id",           "INTEGER PRIMARY KEY", "0"),
        Col("activity_name","TEXT",                "''"),
        Col("input_data",   "TEXT",                "''"),
        Col("output_data",  "TEXT",                "''"),
        Col("error",        "TEXT",                "''"),
        Col("ok",           "INTEGER",             "1"),
        Col("duration_ms",  "REAL",                "0.0"),
        Col("started_at",   "TEXT",                "''"),
        Col("logged_at",    "TEXT",                "''"),
    ]),
}


def compute_row_hash(data: dict, exclude: set = None) -> str:
    """Public wrapper for row hash computation."""
    return _compute_row_hash(data)


class SchemaManager:
    """Validates & auto-heals database schemas on startup."""

    def __init__(self, turso_conn, cloud_conn, tel_conn):
        self.turso = turso_conn
        self.cloud = cloud_conn
        self.tel = tel_conn
        self._sync_ok = True

    @staticmethod
    def _is_snowflake(conn) -> bool:
        """Detect if connection is Snowflake (vs Turso/SQLite)."""
        if conn is None:
            return False
        return hasattr(conn, '_conn') and hasattr(conn._conn, 'rest')

    def _table_exists(self, conn, name: str) -> bool:
        if self._is_snowflake(conn):
            cur = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name = %s",
                (name.upper(),))
            row = cur.fetchone()
            return row[0] > 0 if row else False
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return cur.fetchone() is not None

    def _table_columns(self, conn, table: str) -> List[str]:
        if self._is_snowflake(conn):
            cur = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (table.upper(),))
            return [row[0].lower() for row in cur.fetchall()]
        cur = conn.execute(f"PRAGMA table_info({table})")
        return [row[1] for row in cur.fetchall()]

    def _count_rows(self, conn, table: str) -> int:
        if self._is_snowflake(conn):
            cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
            row = cur.fetchone()
            return row[0] if row else 0
        cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
        row = cur.fetchone()
        return row[0] if row else 0

    def _create_table(self, conn, tbl_name, expected):
        if self._is_snowflake(conn):
            # Snowflake DDL — use Snowflake-compatible types
            type_map = {
                "INTEGER PRIMARY KEY": "INTEGER",
                "TEXT PRIMARY KEY": "TEXT",
                "INTEGER": "INTEGER",
                "REAL": "FLOAT",
                "TEXT": "TEXT",
            }
            cols = []
            pk_col = None
            for c in expected.columns:
                sf_type = type_map.get(c.type, "TEXT")
                cols.append(f"{c.name} {sf_type}")
                if "PRIMARY KEY" in c.type:
                    pk_col = c.name
            if pk_col:
                cols_sql = ", ".join(cols) + f", PRIMARY KEY ({pk_col})"
            else:
                cols_sql = ", ".join(cols)
            conn.execute(f"CREATE TABLE IF NOT EXISTS {tbl_name} ({cols_sql})")
            conn.commit()
        else:
            cols_sql = ", ".join(f"{c.name} {c.type}" for c in expected.columns)
            conn.execute(f"CREATE TABLE {tbl_name} ({cols_sql})")
            conn.commit()
        log.info(f"    Created table: {tbl_name}")

    def _add_missing_columns(self, conn, tbl_name, expected, actual_cols, label):
        for col in expected.columns:
            if col.name not in actual_cols:
                default = "NULL" if col.default == "NULL" else col.default
                if self._is_snowflake(conn):
                    type_map = {
                        "INTEGER PRIMARY KEY": "INTEGER",
                        "TEXT PRIMARY KEY": "TEXT",
                        "INTEGER": "INTEGER",
                        "REAL": "FLOAT",
                        "TEXT": "TEXT",
                    }
                    sf_type = type_map.get(col.type, "TEXT")
                    conn.execute(
                        f"ALTER TABLE {tbl_name} ADD COLUMN {col.name} {sf_type} DEFAULT {default}")
                else:
                    conn.execute(
                        f"ALTER TABLE {tbl_name} ADD COLUMN {col.name} {col.type} DEFAULT {default}")
                log.info(f"    [{label}] Added column {tbl_name}.{col.name} (default={default})")

    def _has_unexpected_columns(self, actual_cols: List[str], expected: TableSchema) -> bool:
        expected_cols = expected.col_names()
        return any(c not in expected_cols for c in actual_cols)

    def _rebuild_table(self, conn, tbl_name: str, expected: TableSchema, label: str):
        """Rebuild a table when it has unexpected columns.

        1. Backup all existing data
        2. Drop the table
        3. Recreate with expected schema
        4. Repopulate from backup (matching columns only)
        5. Compute values for new columns that have compute functions
        """
        is_sf = self._is_snowflake(conn)
        ph = "%s" if is_sf else "?"  # parameter placeholder

        log.info(f"    [{label}] Rebuilding table {tbl_name} (unexpected columns detected)...")

        # 1. Backup
        cur = conn.execute(f"SELECT * FROM {tbl_name}")
        rows = cur.fetchall()
        backup_cols = [d[0] for d in cur.description] if rows else []
        # Normalize column names to lowercase for consistency
        backup_cols = [c.lower() for c in backup_cols]
        backup = [dict(zip(backup_cols, row)) for row in rows]
        log.info(f"    [{label}] Backed up {len(backup)} rows from {tbl_name}")

        # 2. Drop
        conn.execute(f"DROP TABLE {tbl_name}")
        log.info(f"    [{label}] Dropped table {tbl_name}")

        # 3. Recreate
        self._create_table(conn, tbl_name, expected)

        # 4. Repopulate
        expected_col_names = expected.col_names()
        for row_data in backup:
            # Build insert data: only columns that exist in the new schema
            insert_data = {}
            for col_name, value in row_data.items():
                if col_name in expected_col_names:
                    insert_data[col_name] = value

            # 5. Compute values for new columns not in backup
            for col in expected.columns:
                if col.name not in insert_data and col.name != "id":
                    if col.compute:
                        insert_data[col.name] = col.compute(insert_data)
                    else:
                        default = col.default
                        if default == "NULL":
                            insert_data[col.name] = None
                        elif default.startswith("'") and default.endswith("'"):
                            insert_data[col.name] = default[1:-1]
                        else:
                            try:
                                insert_data[col.name] = float(default)
                                if insert_data[col.name] == int(insert_data[col.name]):
                                    insert_data[col.name] = int(insert_data[col.name])
                            except ValueError:
                                insert_data[col.name] = default

            cols_sql = ", ".join(insert_data.keys())
            placeholders = ", ".join([ph] * len(insert_data))
            conn.execute(
                f"INSERT INTO {tbl_name} ({cols_sql}) VALUES ({placeholders})",
                list(insert_data.values()))

        conn.commit()
        log.info(f"    [{label}] Repopulated {tbl_name} with {len(backup)} rows")

    def validate_operational(self):
        conn = self.turso
        for tbl_name, expected in OPERATIONAL_TABLES.items():
            if not self._table_exists(conn, tbl_name):
                self._create_table(conn, tbl_name, expected)
                continue
            actual_cols = self._table_columns(conn, tbl_name)

            if self._has_unexpected_columns(actual_cols, expected):
                self._rebuild_table(conn, tbl_name, expected, "op")
            else:
                self._add_missing_columns(conn, tbl_name, expected, actual_cols, "op")
                conn.commit()

        # Drop unexpected tables (skip system tables like __turso_internal_*)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        all_tables = {row[0] for row in cur.fetchall()}
        for tbl in all_tables - set(OPERATIONAL_TABLES.keys()):
            # Skip Turso internal tables (MVCC metadata, etc.)
            if tbl.startswith("__turso_internal"):
                continue
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")
            log.info(f"    [op] Dropped unexpected table: {tbl}")
        conn.commit()

    def validate_cloud(self):
        conn = self.cloud
        for tbl_name in CLOUD_SYNC_TABLES:
            expected = OPERATIONAL_TABLES[tbl_name]
            if not self._table_exists(conn, tbl_name):
                self._create_table(conn, tbl_name, expected)
                continue
            actual_cols = self._table_columns(conn, tbl_name)

            if self._has_unexpected_columns(actual_cols, expected):
                self._rebuild_table(conn, tbl_name, expected, "cloud")
            else:
                self._add_missing_columns(conn, tbl_name, expected, actual_cols, "cloud")
                conn.commit()
        # Cloud: unexpected tables left alone

    def validate_telemetry(self):
        conn = self.tel
        for tbl_name, expected in TELEMETRY_TABLES.items():
            if not self._table_exists(conn, tbl_name):
                self._create_table(conn, tbl_name, expected)

    def _get_pk_column(self, tbl_name: str) -> str:
        """Get the primary key column name for a table from the expected schema."""
        expected = OPERATIONAL_TABLES.get(tbl_name)
        if expected:
            for col in expected.columns:
                if "PRIMARY KEY" in col.type:
                    return col.name
        return "id"

    def check_sync(self) -> dict:
        """Check sync status for cloud-synced tables using row hashes.

        Returns a dict keyed by table name with:
          op_count, cloud_count, match (bool), hash_mismatches (int)
        """
        result = {}
        for tbl_name in CLOUD_SYNC_TABLES:
            if not self._table_exists(self.turso, tbl_name) or \
               not self._table_exists(self.cloud, tbl_name):
                result[tbl_name] = {
                    "op_count": 0, "cloud_count": 0,
                    "match": False, "hash_mismatches": 0}
                continue

            pk = self._get_pk_column(tbl_name)
            op_n = self._count_rows(self.turso, tbl_name)
            cloud_n = self._count_rows(self.cloud, tbl_name)

            # Compare hashes using the correct PK column
            op_hashes = {}
            cur = self.turso.execute(f"SELECT {pk}, row_hash FROM {tbl_name}")
            for row in cur.fetchall():
                op_hashes[row[0]] = row[1]

            hash_mismatches = 0
            cur = self.cloud.execute(f"SELECT {pk}, row_hash FROM {tbl_name}")
            for row in cur.fetchall():
                cid, chash = row[0], row[1]
                if cid not in op_hashes:
                    hash_mismatches += 1
                elif op_hashes[cid] != chash:
                    hash_mismatches += 1
            # Rows in op but not in cloud
            cloud_ids = set()
            cur = self.cloud.execute(f"SELECT {pk} FROM {tbl_name}")
            for row in cur.fetchall():
                cloud_ids.add(row[0])
            for oid in op_hashes:
                if oid not in cloud_ids:
                    hash_mismatches += 1

            match = (op_n == cloud_n) and (hash_mismatches == 0)
            result[tbl_name] = {
                "op_count": op_n, "cloud_count": cloud_n,
                "match": match, "hash_mismatches": hash_mismatches}
        self._sync_ok = all(v["match"] for v in result.values())
        return result

    @property
    def in_sync(self) -> bool:
        return self._sync_ok
