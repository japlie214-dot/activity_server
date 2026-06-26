# tools/stock_notes/detail_manager.py
"""Detail manager — handles tidy record storage, querying, and concept catalog."""
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("activity-server")


def _get_conn():
    from server.app import get_server
    server = get_server()
    if server is None:
        raise RuntimeError("Server not initialized")
    return server.turso


def validate_quarter_date(date_str: str) -> Tuple[bool, str]:
    if not date_str:
        return True, ""
    if not re.match(r'^\d{4}-(?:0[1-9]|1[0-2])$', date_str):
        return False, f"Invalid date format '{date_str}'. Expected YYYY-MM (e.g., 2025-03)."
    return True, ""


def upsert_tidy_records(records: List[Dict[str, Any]]) -> int:
    """Insert or replace tidy records into sn_note_details."""
    if not records:
        return 0

    conn = _get_conn()
    now_iso = datetime.now(timezone.utc).isoformat()

    columns = [
        "detail_id", "accession_no", "note_number", "detail_index", "ticker", "form",
        "concept", "label", "standard_concept", "level", "abstract", "dimension",
        "is_breakdown", "dimension_axis", "dimension_member", "dimension_member_label",
        "dimension_label", "balance", "weight", "preferred_sign", "parent_concept",
        "parent_abstract_concept", "period_raw", "period_end_date", "period_type",
        "value", "row_order", "content_hash", "extracted_at", "created_at", "updated_at"
    ]

    sql = f'INSERT OR REPLACE INTO sn_note_details ({", ".join(columns)}) VALUES ({", ".join(["?"] * len(columns))})'

    for r in records:
        r["extracted_at"] = r.get("extracted_at") or now_iso
        r["created_at"] = r.get("created_at") or now_iso
        r["updated_at"] = r.get("updated_at") or now_iso
        vals = []
        for c in columns:
            val = r.get(c)
            if val is None:
                if c in ("note_number", "detail_index", "level", "row_order"):
                    vals.append(0)
                else:
                    vals.append("")
            else:
                vals.append(val)
        conn.execute(sql, tuple(vals))

    conn.commit()
    return len(records)


def query_tidy_table(
    ticker: str, concept: str, start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> List[Dict[str, Any]]:
    if start_date:
        valid, err = validate_quarter_date(start_date)
        if not valid:
            raise ValueError(err)
    if end_date:
        valid, err = validate_quarter_date(end_date)
        if not valid:
            raise ValueError(err)

    conn = _get_conn()
    where_parts = ["ticker = ?", "concept = ?"]
    params = [ticker, concept]

    if start_date:
        where_parts.append("period_end_date >= ?")
        params.append(f"{start_date}-01")
    if end_date:
        where_parts.append("period_end_date <= ?")
        params.append(f"{end_date}-31")

    sql = f'SELECT period_end_date, period_type, value, label, dimension_label FROM sn_note_details WHERE {" AND ".join(where_parts)} ORDER BY period_end_date DESC LIMIT 500'
    cur = conn.execute(sql, tuple(params))
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def format_as_markdown_table(records: List[Dict[str, Any]], table_name: str) -> str:
    if not records:
        return f"**{table_name}**: (no data found for selected date range)"
    all_cols = list(records[0].keys())
    display_cols = [c for c in all_cols if not c.startswith('_')] or all_cols
    lines = ["| " + " | ".join(display_cols) + " |",
             "| " + " | ".join("---" for _ in display_cols) + " |"]
    for record in records:
        lines.append("| " + " | ".join(str(record.get(col, "")) for col in display_cols) + " |")
    return "\n".join(lines)


def delete_filing_data(accession_no: str) -> int:
    """Delete all data for a filing (for force-refresh)."""
    conn = _get_conn()
    count = conn.execute(
        "SELECT COUNT(*) FROM sn_note_details WHERE accession_no = ?", (accession_no,)
    ).fetchone()[0]

    if count > 0:
        conn.execute("DELETE FROM sn_note_details WHERE accession_no = ?", (accession_no,))
    conn.execute("DELETE FROM sn_detail_registry WHERE source_accession_no = ?", (accession_no,))
    conn.execute("DELETE FROM sn_notes WHERE accession_no = ?", (accession_no,))
    conn.commit()
    return count


def build_concept_catalog(ticker: str, accession_no: str, note_number: int) -> list:
    conn = _get_conn()
    cur = conn.execute("""
        SELECT concept, label, dimension_axis, dimension_member_label,
               COUNT(DISTINCT period_end_date) as period_count,
               MIN(period_end_date) as earliest_period,
               MAX(period_end_date) as latest_period,
               period_type, value
        FROM sn_note_details
        WHERE ticker = ? AND accession_no = ? AND note_number = ?
          AND abstract = 'False' AND value != ''
        GROUP BY concept, dimension_member_label
        ORDER BY concept, dimension_member_label
        LIMIT 50
    """, (ticker, accession_no, note_number))

    catalog = []
    for row in cur.fetchall():
        concept, label, dim_axis, dim_member, pcount, earliest, latest, ptype, value = row
        catalog.append({
            "concept": concept, "label": label,
            "dimension_axis": dim_axis or "",
            "dimension_member_label": dim_member or "",
            "period_count": pcount,
            "earliest_period": earliest or "",
            "latest_period": latest or "",
            "period_type": ptype,
            "sample_value": _format_sample_value(value),
        })
    return catalog


def _format_sample_value(value: str) -> str:
    try:
        num = float(value)
        if abs(num) >= 1e9: return f"{num/1e9:.1f}B"
        if abs(num) >= 1e6: return f"{num/1e6:.1f}M"
        if abs(num) >= 1e3: return f"{num/1e3:.1f}K"
        return f"{num:.2f}"
    except (ValueError, TypeError):
        return str(value)[:20]


def get_date_range_for_filing(accession_no: str) -> Tuple[str, str]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT period_of_report, fiscal_year_end_month FROM sn_filings WHERE accession_no = ?",
        (accession_no,)
    ).fetchone()
    if not row or not row[0]:
        return "", ""
    period_str = row[0][:10]
    fy_month = row[1] or 12
    try:
        period_date = datetime.strptime(period_str, "%Y-%m-%d")
        end_date = period_date.strftime("%Y-%m")
        start_date = f"{period_date.year - 2:04d}-{fy_month:02d}"
        return start_date, end_date
    except ValueError:
        return "", ""


def register_detail_table(
    ticker: str, detail_table_name: str, source_title: str, source_note_number: int,
    source_accession_no: str, role_or_type: str, unique_concepts: List[str], row_count: int,
    quarter: int, year: int, quarterly_status: str
):
    from .tidy_transform import make_registry_id

    conn = _get_conn()
    rid = make_registry_id(ticker, detail_table_name, source_accession_no, source_note_number)
    now_iso = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """INSERT OR REPLACE INTO sn_detail_registry
           (registry_id, ticker, detail_table_name, source_title, source_note_number,
            source_accession_no, role_or_type, available_concepts, tidy_schema_version,
            row_count, quarter, year, quarterly_status, content_hash, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, '', ?, ?)""",
        (rid, ticker, detail_table_name, source_title, source_note_number,
         source_accession_no, role_or_type, json.dumps(unique_concepts),
         row_count, quarter, year, quarterly_status, now_iso, now_iso)
    )
    conn.commit()
