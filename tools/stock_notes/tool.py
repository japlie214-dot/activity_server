# tools/stock_notes/tool.py
"""Stock Notes Tool — SEC EDGAR footnote extraction and analysis.

Activities:
  1. stock_notes.validate — Parse and validate command + instructions
  2. stock_notes.execute  — Run the appropriate sub-command
  3. stock_notes.format   — Render results as markdown
"""
import json
import os
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


@Activity("stock_notes.validate")
def validate(acc, arguments: dict) -> dict:
    """Validate and normalize command + instructions."""
    command = arguments.get("command", "").strip().lower()
    if command not in ("discover", "note", "details"):
        raise ValueError(f"Invalid command '{command}'. Use: discover, note, details")

    raw_inst = arguments.get("instructions", {})
    if isinstance(raw_inst, str):
        try:
            instructions = json.loads(raw_inst)
        except Exception:
            raise ValueError("Instructions must be a valid JSON object")
    else:
        instructions = raw_inst or {}

    return {"command": command, "instructions": instructions}


@Activity("stock_notes.execute")
def execute_command(acc, validated: dict) -> Any:
    """Dispatch to the appropriate sub-command handler."""
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
    """Format the result as a dict for the response."""
    if isinstance(result, dict) and "error" in result:
        return result
    return result


# ── discover ─────────────────────────────────────────────────────────

def _do_discover(inst: dict) -> dict:
    from .extractor import discover_filings

    ticker = (inst.get("ticker") or "").upper().strip()
    if not ticker:
        return {"error": "Missing ticker. Provide a ticker symbol."}

    forms = inst.get("forms", "10-K,10-Q")
    form_types = [f.strip() for f in forms.split(",") if f.strip()]
    filings = discover_filings(ticker, form_types=form_types, limit=40)

    if not filings:
        return {"error": f"No filings found for {ticker}. Verify the ticker."}

    lines = [f"# Filings for {ticker} ({len(filings)} found, newest first)\n"]
    lines.append("| # | Form | Filing Date | Period | Quarter | Accession No |")
    lines.append("|---|------|-------------|--------|---------|--------------|")
    for i, f in enumerate(filings):
        q_str = f"Q{f['quarter']} FY{f['year']}" if f.get("quarter") else "N/A"
        lines.append(f"| {i+1} | {f['form']} | {f['filing_date']} | {f.get('period_of_report', 'N/A')} | {q_str} | {f['accession_no']} |")
    lines.append(f"\nUse `note` command with `{{\"accession_no\": \"<accession_no>\"}}` to list notes.")

    return {"ticker": ticker, "filings_count": len(filings), "markdown": "\n".join(lines)}


# ── note ─────────────────────────────────────────────────────────────

def _do_note(inst: dict) -> dict:
    from .extractor import extract_and_persist_filing

    accession_no = (inst.get("accession_no") or "").strip()
    if not accession_no:
        return {"error": "Missing accession_no."}

    ticker = (inst.get("ticker") or "").upper().strip()
    note_number = inst.get("note_number")
    force_refresh = bool(inst.get("force_refresh", False))

    conn = _get_conn()

    # Cache-first: extract only if not already local or force_refresh
    exists = conn.execute("SELECT 1 FROM sn_filings WHERE accession_no=?", (accession_no,)).fetchone()
    if force_refresh or not exists:
        extract_and_persist_filing(accession_no, ticker=ticker, force_refresh=force_refresh)

    # Auto-hydrate specific note if missing
    if note_number is not None:
        note_exists = conn.execute(
            "SELECT 1 FROM sn_notes WHERE accession_no=? AND note_number=? LIMIT 1",
            (accession_no, note_number)
        ).fetchone()
        if not note_exists and not force_refresh:
            extract_and_persist_filing(accession_no, ticker=ticker, force_refresh=False)

    # Fetch filing metadata
    filing_row = conn.execute(
        "SELECT ticker, form, company_name, period_of_report, quarter, year, fiscal_year_end_month "
        "FROM sn_filings WHERE accession_no=?", (accession_no,)
    ).fetchone()
    if not filing_row:
        return {"error": f"Filing {accession_no} not found after extraction."}

    f_ticker, f_form, f_company, f_period, f_quarter, f_year, f_fye = filing_row

    if note_number is None:
        # List all notes
        notes = conn.execute(
            "SELECT note_number, title, short_name, table_count, details_count, quarterly_status "
            "FROM sn_notes WHERE accession_no=? ORDER BY note_number", (accession_no,)
        ).fetchall()
        if not notes:
            return {"message": f"No notes found in filing {accession_no}.", "accession_no": accession_no}

        lines = [f"# Notes in {f_form} Filing: {f_company} ({f_ticker})",
                 f"**Accession:** {accession_no} | **Period:** {f_period} | Q{f_quarter} FY{f_year}\n"]
        lines.append("| Note# | Title | Tables | Details | Q Status | Concepts Preview |")
        lines.append("|-------|-------|--------|---------|----------|------------------|")
        for n in notes:
            concept_str = ""
            if n[4] > 0:
                concepts = conn.execute(
                    "SELECT DISTINCT concept FROM sn_note_details WHERE accession_no=? AND note_number=? AND abstract='False' AND value!='' LIMIT 5",
                    (accession_no, n[0])
                ).fetchall()
                if concepts:
                    concept_str = ", ".join(c[0].replace("us-gaap:", "") for c in concepts)
            lines.append(f"| {n[0]} | {n[1]} | {n[3]} | {n[4]} | {n[5]} | {concept_str} |")

        return {"accession_no": accession_no, "notes_count": len(notes), "markdown": "\n".join(lines)}

    # Drill into specific note
    note_row = conn.execute(
        "SELECT note_number, title, short_name, narrative_text, expands, expands_statements, "
        "table_count, details_count, quarterly_status "
        "FROM sn_notes WHERE accession_no=? AND note_number=?",
        (accession_no, note_number)
    ).fetchone()
    if not note_row:
        return {"error": f"Note {note_number} not found in {accession_no}."}

    n_num, n_title, n_short, n_narrative, n_expands, n_expands_stmts, n_tbl_count, n_dt_count, n_q_status = note_row

    dts = conn.execute(
        "SELECT detail_table_name, source_title, role_or_type, available_concepts "
        "FROM sn_detail_registry WHERE source_accession_no=? AND source_note_number=?",
        (accession_no, note_number)
    ).fetchall()

    lines = [f"# Note {n_num}: {n_title}",
             f"**Company:** {f_company} ({f_ticker})",
             f"**Accession:** {accession_no} | **Form:** {f_form}",
             f"**Quarter:** Q{f_quarter} FY{f_year} | **Status:** {n_q_status}",
             f"**Tables:** {n_tbl_count} | **Details:** {n_dt_count} | **Detail Tables:** {len(dts)}"]

    if n_narrative:
        display = n_narrative if len(n_narrative) <= 200000 else n_narrative[:200000] + "\n\n...[Truncated]"
        lines.append(f"\n## Narrative\n\n{display}")
    else:
        lines.append("\n*No narrative content available.*")

    if dts:
        from .detail_manager import build_concept_catalog, get_date_range_for_filing
        catalog = build_concept_catalog(f_ticker, accession_no, note_number)
        start_date, end_date = get_date_range_for_filing(accession_no)

        if catalog:
            lines.append(f"\n## Concept Catalog ({len(catalog)} queryable concepts)\n")
            lines.append("| # | Concept | Label | Axis | Member | Periods | Range |")
            lines.append("|---|---------|-------|------|--------|---------|-------|")
            for ci, entry in enumerate(catalog, 1):
                axis_short = (entry.get("dimension_axis", "") or "").replace("us-gaap:", "").replace("Axis", "") or "\u2014"
                member = entry.get("dimension_member_label") or "\u2014"
                er = entry.get("earliest_period", "")[:7] if entry.get("earliest_period") else "?"
                lr = entry.get("latest_period", "")[:7] if entry.get("latest_period") else "?"
                pc = entry.get("period_count", "?")
                lines.append(f"| {ci} | `{entry['concept']}` | {entry['label']} | {axis_short} | {member} | {pc} | {er} \u2192 {lr} |")

            distinct_concepts = list(dict.fromkeys(e["concept"] for e in catalog))
            lines.append(f"\n**Quick Queries** (copy into `details` command):")
            for c in distinct_concepts[:5]:
                lines.append(f'- `{{"ticker": "{f_ticker}", "concept": "{c}", "start_date": "{start_date}", "end_date": "{end_date}"}}`')
            if len(distinct_concepts) > 5:
                lines.append(f"\n...and {len(distinct_concepts) - 5} more concepts.")
        else:
            lines.append("\n*No queryable concepts found in this note.*")
    else:
        lines.append("\n*No detail concepts found for this note.*")

    return {"accession_no": accession_no, "note_number": note_number, "markdown": "\n".join(lines)}


# ── details ──────────────────────────────────────────────────────────

def _do_details(inst: dict) -> dict:
    from .detail_manager import query_tidy_table, format_as_markdown_table

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
        return {"error": f"No records found for concept '{concept}' on {ticker}. Adjust date range or extract notes first."}

    md_table = format_as_markdown_table(records, f"Time Series: {concept}")
    lines = [f"# Concept Details: {concept} ({ticker})", f"**Records:** {len(records)}"]
    if start_date and end_date:
        lines.append(f"**Date range:** {start_date} to {end_date}")
    lines.extend(["", md_table])

    return {"ticker": ticker, "concept": concept, "records": len(records), "markdown": "\n".join(lines)}


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
                "description": "Command-specific parameters. For discover: {ticker, forms?}. For note: {accession_no, note_number?, force_refresh?}. For details: {ticker, concept, start_date?, end_date?}.",
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
