# tools/stock_notes/tool.py
"""Stock Notes Tool — SEC EDGAR footnote extraction and analysis.

Activities:
  1. stock_notes.validate — Parse and validate command + instructions
  2. stock_notes.execute  — Run the appropriate sub-command (discover/note/details)
  3. stock_notes.format   — Normalize result into response dict
"""
import json
import hashlib
from datetime import datetime, timezone
from typing import Any

from tools import Tool
from server.accumulator import Activity
from .config import TOOL_NAME
from .docs import TOOL_DESCRIPTION, TOOL_DOCS, TOOL_OUTPUT_EXAMPLE


def _get_conn():
    from server.app import get_server
    server = get_server()
    if server is None:
        raise RuntimeError("Server not initialized")
    return server.turso


# ── Activities ───────────────────────────────────────────────────────

@Activity("stock_notes.validate")
def validate(acc, arguments: dict) -> dict:
    """Parse and validate command + instructions.

    Normalizes ticker to uppercase, parses JSON string instructions,
    and validates the command is one of: discover, note, details.
    Standardizes 'force_refresh' to 'refresh' for backward compatibility.
    """
    command = arguments.get("command", "").strip().lower()
    if command not in ("discover", "note", "details"):
        raise ValueError(
            f"Invalid command '{command}'. Use: discover, note, details"
        )

    raw_inst = arguments.get("instructions", {})
    if isinstance(raw_inst, str):
        try:
            instructions = json.loads(raw_inst)
        except Exception:
            raise ValueError("Instructions must be a valid JSON object")
    else:
        instructions = raw_inst or {}

    # Standardize: accept both 'refresh' and 'force_refresh', normalize to 'refresh'
    if "force_refresh" in instructions and "refresh" not in instructions:
        instructions["refresh"] = instructions.pop("force_refresh")

    return {"command": command, "instructions": instructions}


@Activity("stock_notes.execute")
def execute_command(acc, validated: dict) -> Any:
    """Dispatch to the appropriate sub-command handler.

    Routes to: _do_discover, _do_note, or _do_details.
    """
    cmd = validated["command"]
    inst = validated["instructions"]

    if cmd == "discover":
        return _do_discover(inst)
    elif cmd == "note":
        return _do_note(inst)
    elif cmd == "details":
        return _do_details(inst)
    return {"error": f"Unknown command: {cmd}"}


@Activity("stock_notes.format")
def format_result(acc, result: Any, validated: dict) -> dict:
    """Normalize the result into a response dict.

    Ensures consistent JSON structure. Passes through error dicts unchanged.
    """
    if isinstance(result, dict) and "error" in result:
        return result
    return result


# ── discover ─────────────────────────────────────────────────────────

def _do_discover(inst: dict) -> dict:
    """Find and list recent filings for a ticker.

    Always calls EDGAR to get the latest filings, then merges into sn_filings
    so the `note` command benefits from the cache. SEC filings are immutable
    once filed — merging is safe (INSERT OR IGNORE skips duplicates).
    """
    from .extractor import discover_filings

    ticker = (inst.get("ticker") or "").upper().strip()
    if not ticker:
        return {"error": "Missing ticker. Provide a ticker symbol."}

    forms = inst.get("forms", "10-K,10-Q")
    form_types = [f.strip() for f in forms.split(",") if f.strip()]

    # Always call EDGAR for discover
    filings = discover_filings(ticker, form_types=form_types, limit=40)

    if not filings:
        return {"error": f"No filings found for {ticker}. Verify the ticker."}

    # Merge discovered filings into sn_filings for caching
    conn = _get_conn()
    dw = None
    try:
        from server.app import get_server
        server = get_server()
        if server:
            dw = server.dual_writer
    except Exception:
        pass

    now_iso = datetime.now(timezone.utc).isoformat()
    for f in filings:
        filing_id = f"{ticker}|{f['form']}|{f['accession_no']}"
        filing_data = {
            "filing_id": filing_id,
            "ticker": ticker,
            "form": f["form"],
            "filing_date": f["filing_date"],
            "accession_no": f["accession_no"],
            "period_of_report": f.get("period_of_report", ""),
            "company_name": f.get("company_name", ""),
            "cik": str(f.get("cik", "")),
            "fiscal_year_end_month": f.get("fiscal_year_end_month", 12),
            "quarter": f.get("quarter", 0),
            "year": f.get("year", 0),
            "content_hash": hashlib.md5(filing_id.encode()).hexdigest(),
            "updated_at": now_iso,
        }
        if dw:
            dw.upsert("sn_filings", filing_data)
        else:
            conn.execute(
                "INSERT OR IGNORE INTO sn_filings "
                "(filing_id, ticker, form, filing_date, accession_no, period_of_report, "
                "company_name, cik, fiscal_year_end_month, quarter, year, content_hash, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (filing_id, ticker, f["form"], f["filing_date"], f["accession_no"],
                 f.get("period_of_report", ""), f.get("company_name", ""),
                 str(f.get("cik", "")), f.get("fiscal_year_end_month", 12),
                 f.get("quarter", 0), f.get("year", 0),
                 hashlib.md5(filing_id.encode()).hexdigest(), now_iso))
    conn.commit()

    filing_list = []
    for f in filings:
        q_str = f"Q{f['quarter']} FY{f['year']}" if f.get("quarter") else None
        filing_list.append({
            "form": f["form"],
            "filing_date": f["filing_date"],
            "period_of_report": f.get("period_of_report", ""),
            "quarter": f.get("quarter", 0),
            "year": f.get("year", 0),
            "quarter_label": q_str,
            "accession_no": f["accession_no"],
        })

    return {
        "ticker": ticker,
        "filings_count": len(filing_list),
        "filings": filing_list,
    }


# ── note ─────────────────────────────────────────────────────────────

def _do_note(inst: dict) -> dict:
    """List all notes in a filing, or drill into a specific note.

    With note_number=None: returns list of all notes with metadata.
    With note_number set: returns full note detail with narrative and concept catalog.

    Uses 'refresh' (not 'force_refresh') to control re-extraction.
    """
    from .extractor import extract_and_persist_filing

    accession_no = (inst.get("accession_no") or "").strip()
    if not accession_no:
        return {"error": "Missing accession_no."}

    ticker = (inst.get("ticker") or "").upper().strip()
    note_number = inst.get("note_number")
    refresh = bool(inst.get("refresh", False))

    conn = _get_conn()

    # Cache-first: extract only if not already local or refresh
    exists = conn.execute(
        "SELECT 1 FROM sn_filings WHERE accession_no=?", (accession_no,)
    ).fetchone()
    if refresh or not exists:
        extract_and_persist_filing(
            accession_no, ticker=ticker, force_refresh=refresh
        )

    # Auto-hydrate if notes are missing (filing exists but notes not extracted yet)
    if not refresh:
        notes_exist = conn.execute(
            "SELECT 1 FROM sn_notes WHERE accession_no=? LIMIT 1",
            (accession_no,),
        ).fetchone()
        if not notes_exist:
            extract_and_persist_filing(
                accession_no, ticker=ticker, force_refresh=False
            )

    # Fetch filing metadata
    filing_row = conn.execute(
        "SELECT ticker, form, company_name, period_of_report, quarter, year, "
        "fiscal_year_end_month FROM sn_filings WHERE accession_no=?",
        (accession_no,),
    ).fetchone()
    if not filing_row:
        return {"error": f"Filing {accession_no} not found after extraction."}

    f_ticker, f_form, f_company, f_period, f_quarter, f_year, f_fye = filing_row

    if note_number is None:
        # List all notes
        notes = conn.execute(
            "SELECT note_number, title, short_name, table_count, details_count, "
            "quarterly_status FROM sn_notes WHERE accession_no=? ORDER BY note_number",
            (accession_no,),
        ).fetchall()
        if not notes:
            return {
                "message": f"No notes found in filing {accession_no}.",
                "accession_no": accession_no,
            }

        note_list = []
        for n in notes:
            concept_preview = []
            if n[4] > 0:
                concepts = conn.execute(
                    "SELECT DISTINCT concept FROM sn_note_details "
                    "WHERE accession_no=? AND note_number=? "
                    "AND abstract='False' AND value!='' LIMIT 5",
                    (accession_no, n[0]),
                ).fetchall()
                concept_preview = [
                    c[0].replace("us-gaap:", "") for c in concepts
                ]
            note_list.append({
                "note_number": n[0],
                "title": n[1],
                "short_name": n[2],
                "table_count": n[3],
                "details_count": n[4],
                "quarterly_status": n[5],
                "concept_preview": concept_preview,
            })

        return {
            "accession_no": accession_no,
            "ticker": f_ticker,
            "form": f_form,
            "company": f_company,
            "period": f_period,
            "quarter": f_quarter,
            "year": f_year,
            "notes_count": len(note_list),
            "notes": note_list,
        }

    # Drill into specific note
    note_row = conn.execute(
        "SELECT note_number, title, short_name, narrative_text, expands, "
        "expands_statements, table_count, details_count, quarterly_status "
        "FROM sn_notes WHERE accession_no=? AND note_number=?",
        (accession_no, note_number),
    ).fetchone()
    if not note_row:
        return {"error": f"Note {note_number} not found in {accession_no}."}

    (
        n_num, n_title, n_short, n_narrative, n_expands,
        n_expands_stmts, n_tbl_count, n_dt_count, n_q_status,
    ) = note_row

    dts = conn.execute(
        "SELECT detail_table_name, source_title, role_or_type, available_concepts "
        "FROM sn_detail_registry WHERE source_accession_no=? AND source_note_number=?",
        (accession_no, note_number),
    ).fetchall()

    detail_tables = []
    for dt in dts:
        detail_tables.append({
            "detail_table_name": dt[0],
            "source_title": dt[1],
            "role_or_type": dt[2],
            "available_concepts": json.loads(dt[3]) if dt[3] else [],
        })

    # Build concept catalog if details exist
    concept_catalog = []
    quick_queries = []
    if dts:
        from .detail_manager import build_concept_catalog, get_date_range_for_filing

        catalog = build_concept_catalog(f_ticker, accession_no, note_number)
        start_date, end_date = get_date_range_for_filing(accession_no)

        for entry in catalog:
            concept_catalog.append({
                "concept": entry["concept"],
                "label": entry["label"],
                "dimension_axis": entry.get("dimension_axis", ""),
                "dimension_member_label": entry.get("dimension_member_label", ""),
                "period_count": entry.get("period_count", 0),
                "earliest_period": entry.get("earliest_period", ""),
                "latest_period": entry.get("latest_period", ""),
                "period_type": entry.get("period_type", ""),
                "sample_value": entry.get("sample_value", ""),
            })

        distinct_concepts = list(
            dict.fromkeys(e["concept"] for e in catalog)
        )
        for c in distinct_concepts[:5]:
            quick_queries.append({
                "ticker": f_ticker,
                "concept": c,
                "start_date": start_date,
                "end_date": end_date,
            })

    # Truncate narrative if extremely long
    narrative_display = n_narrative
    narrative_truncated = False
    if n_narrative and len(n_narrative) > 200000:
        narrative_display = n_narrative[:200000]
        narrative_truncated = True

    return {
        "accession_no": accession_no,
        "ticker": f_ticker,
        "form": f_form,
        "company": f_company,
        "period": f_period,
        "quarter": f_quarter,
        "year": f_year,
        "note_number": n_num,
        "title": n_title,
        "short_name": n_short,
        "quarterly_status": n_q_status,
        "table_count": n_tbl_count,
        "details_count": n_dt_count,
        "narrative": narrative_display,
        "narrative_truncated": narrative_truncated,
        "expands": json.loads(n_expands) if n_expands else [],
        "expands_statements": json.loads(n_expands_stmts) if n_expands_stmts else [],
        "detail_tables": detail_tables,
        "concept_catalog": concept_catalog,
        "quick_queries": quick_queries,
    }


# ── details ──────────────────────────────────────────────────────────

def _do_details(inst: dict) -> dict:
    """Extract time-series data for a specific XBRL concept.

    Returns structured JSON with records (no markdown).
    """
    from .detail_manager import query_tidy_table

    ticker = (inst.get("ticker") or "").upper().strip()
    concept = (inst.get("concept") or "").strip()
    if not ticker or not concept:
        return {"error": "Missing ticker or concept."}

    start_date = (inst.get("start_date") or "").strip() or None
    end_date = (inst.get("end_date") or "").strip() or None

    try:
        records = query_tidy_table(ticker, concept, start_date, end_date)
    except ValueError as ve:
        return {"error": str(ve)}

    if not records:
        return {
            "error": (
                f"No records found for concept '{concept}' on {ticker}. "
                "Adjust date range or extract notes first."
            )
        }

    return {
        "ticker": ticker,
        "concept": concept,
        "start_date": start_date,
        "end_date": end_date,
        "records_count": len(records),
        "records": records,
    }


# ── Tool class ───────────────────────────────────────────────────────


class StockNotesTool(Tool):
    name = TOOL_NAME
    description = TOOL_DESCRIPTION
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "One of: discover, note, details",
                "enum": ["discover", "note", "details"],
            },
            "instructions": {
                "type": "object",
                "description": (
                    "Command-specific parameters. "
                    "For discover: {ticker, forms?}. "
                    "For note: {accession_no, note_number?, ticker?, refresh?}. "
                    "For details: {ticker, concept, start_date?, end_date?}."
                ),
            },
        },
        "required": ["command", "instructions"],
    }

    def execute(self, arguments: dict, acc=None) -> dict:
        validated = validate(acc, arguments)
        result = execute_command(acc, validated)
        return format_result(acc, result, validated)

    @classmethod
    def docs(cls) -> dict:
        return {
            "summary": cls.description,
            "description": TOOL_DOCS,
            "input_schema": cls.input_schema,
            "output_example": TOOL_OUTPUT_EXAMPLE,
        }
