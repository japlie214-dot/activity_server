# tools/stock_financials/tool.py
"""Stock Financials Tool — SEC EDGAR quarterly fact extraction.

Activities:
  1. stock_financials.validate — Parse and validate command + instructions
  2. stock_financials.execute  — Run the appropriate sub-command
  3. stock_financials.format   — Render results as markdown
"""
import asyncio
import json
import sqlite3
from typing import Any, Dict, List

from tools import Tool
from server.accumulator import Activity
from .config import TOOL_NAME
from .docs import TOOL_DESCRIPTION, TOOL_DOCS, TOOL_OUTPUT_EXAMPLE


# ── Presentation constants ───────────────────────────────────────────

STATEMENT_TYPES = {"income": "Income Statement", "balance": "Balance Sheet", "cashflow": "Cash Flow Statement"}
PER_SHARE_UNITS = frozenset({"USD per share", "USD/shares", "TWD per share", "JPY per share", "EUR per share", "GBP per share"})
SHARE_UNITS = frozenset({"shares"})
_CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "TWD": "NT$", "CNY": "¥", "KRW": "₩", "INR": "₹", "AUD": "A$", "CAD": "C$", "CHF": "CHF", "SGD": "S$", "HKD": "HK$"}
KEY_CONCEPTS = {
    "income": {"us-gaap:Revenues": "Revenue", "us-gaap:GrossProfit": "Gross Profit", "us-gaap:OperatingIncomeLoss": "Operating Income", "us-gaap:NetIncomeLoss": "Net Income", "us-gaap:EarningsPerShareBasic": "EPS (Basic)"},
    "balance": {"us-gaap:Assets": "Total Assets", "us-gaap:Liabilities": "Total Liabilities", "us-gaap:StockholdersEquity": "Stockholders' Equity", "us-gaap:CashAndCashEquivalentsAtCarryingValue": "Cash & Equivalents"},
    "cashflow": {"us-gaap:NetCashProvidedByUsedInOperatingActivities": "Operating CF", "us-gaap:NetCashProvidedByUsedInInvestingActivities": "Investing CF"},
}
SUMMARY_QUARTERS_SHOWN = 4
SUMMARY_CHAR_BUDGET = 18_000


def _extract_currency_code(unit: str) -> str | None:
    if not unit: return None
    first = unit.strip().split()[0].split("/")[0].upper()
    return None if first in {"SHARES", "PURE", ""} else first

def _currency_symbol(code: str | None) -> str:
    if not code: return "$"
    return _CURRENCY_SYMBOLS.get(code, code)

def format_value(value, unit: str = "USD") -> str:
    if value is None: return "—"
    try: val = float(value)
    except (TypeError, ValueError): return str(value)
    code = _extract_currency_code(unit)
    sym = _currency_symbol(code)
    if unit in PER_SHARE_UNITS:
        return f"{sym}{val:,.2f}" if abs(val) >= 0.01 else f"{sym}{val:.4f}"
    if unit in SHARE_UNITS:
        if abs(val) >= 1e9: return f"{val/1e9:,.2f}B"
        if abs(val) >= 1e6: return f"{val/1e6:,.2f}M"
        return f"{val:,.0f}"
    if abs(val) >= 1e9: return f"{sym}{val/1e9:,.1f}B"
    if abs(val) >= 1e6: return f"{sym}{val/1e6:,.1f}M"
    return f"{sym}{val:,.0f}"


def _get_conn():
    """Get a turso connection to the operational database."""
    from server.app import get_server
    server = get_server()
    if server is None:
        raise RuntimeError("Server not initialized")
    return server.turso


def _ensure_indexes():
    """Create composite unique index for sf_quarterly_facts if not exists."""
    conn = _get_conn()
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_sf_quarterly_facts_key
                    ON sf_quarterly_facts(ticker, statement_type, concept, quarter)""")
    conn.commit()


# ── Activities ───────────────────────────────────────────────────────

@Activity("stock_financials.validate")
def validate(acc, arguments: dict) -> dict:
    """Validate and normalize command + instructions."""
    command = arguments.get("command", "").strip().lower()
    if command not in ("extract", "query", "status", "catalog"):
        raise ValueError(f"Invalid command '{command}'. Use: extract, query, status, catalog")
    instructions = arguments.get("instructions", {})
    if isinstance(instructions, str):
        try: instructions = json.loads(instructions)
        except Exception: raise ValueError("Instructions must be a valid JSON object")
    ticker = (instructions.get("ticker") or "").upper().strip()
    if not ticker and command != "catalog":
        raise ValueError("Missing required field: ticker")
    return {"command": command, "ticker": ticker, "instructions": instructions}


@Activity("stock_financials.execute")
def execute_command(acc, validated: dict) -> Any:
    """Dispatch to the appropriate sub-command handler."""
    cmd = validated["command"]
    ticker = validated["ticker"]
    inst = validated["instructions"]

    if cmd == "extract":
        return _do_extract(ticker, inst)
    elif cmd == "query":
        return _do_query(ticker, inst)
    elif cmd == "status":
        return _do_status(ticker)
    elif cmd == "catalog":
        return _do_catalog(ticker, inst)
    return {"error": f"Unknown command: {cmd}"}


@Activity("stock_financials.format")
def format_result(acc, result: Any, validated: dict) -> dict:
    """Format the result as a dict for the response."""
    if isinstance(result, dict) and "error" in result:
        return result
    return result


# ── Sub-command implementations ──────────────────────────────────────

def _do_extract(ticker: str, inst: dict) -> dict:
    """Extract quarterly financials from EDGAR and persist to DB."""
    from .extractor import extract_and_persist

    quarters = min(int(inst.get("quarters", 8)), 40)
    refresh = bool(inst.get("refresh", False))
    _ensure_indexes()

    conn = _get_conn()
    existing = conn.execute(
        "SELECT COUNT(DISTINCT quarter) FROM sf_quarterly_facts WHERE ticker=?",
        (ticker,)
    ).fetchone()
    cache_hit = existing[0] >= quarters and not refresh

    if not cache_hit:
        result = extract_and_persist(ticker, quarters, refresh)
    else:
        result = {"ticker": ticker, "company": ticker, "persisted": {}, "cache_hit": True}

    # Fetch all rows for summary
    rows = _fetch_rows(ticker)
    if not rows:
        return {"ticker": ticker, "message": "No data extracted", "rows": 0}

    company_name = _fetch_company_name(ticker) or ticker
    quarters_found = len({r["quarter"] for r in rows})

    # Build summary markdown
    md = _build_extract_markdown(ticker, company_name, quarters, quarters_found, cache_hit, refresh, rows)
    return {"ticker": ticker, "company": company_name, "quarters": quarters_found, "rows": len(rows), "cache_hit": cache_hit, "markdown": md}


def _do_query(ticker: str, inst: dict) -> dict:
    """Query cached financial facts."""
    from .query import query_facts
    _ensure_indexes()

    statement_type = inst.get("statement_type", "income")
    concept = inst.get("concept")
    start_q = inst.get("start_quarter")
    end_q = inst.get("end_quarter")
    limit = min(int(inst.get("limit", 100)), 500)

    records = query_facts(ticker, statement_type, concept, start_q, end_q, limit)
    if not records:
        return {"ticker": ticker, "statement_type": statement_type, "records": 0, "message": "No records found. Run extract first."}

    md = _build_query_markdown(ticker, statement_type, concept, records)
    return {"ticker": ticker, "statement_type": statement_type, "records": len(records), "markdown": md}


def _do_status(ticker: str) -> dict:
    """Check cache status for a ticker."""
    _ensure_indexes()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT statement_type, COUNT(DISTINCT quarter) as q_count, "
        "COUNT(*) as r_count, MAX(quarter) as latest "
        "FROM sf_quarterly_facts WHERE ticker=? GROUP BY statement_type",
        (ticker,)
    ).fetchall()

    if not rows:
        return {"ticker": ticker, "message": "No cached data. Run extract first.", "statements": {}}

    statements = {}
    for row in rows:
        stype = row[0]
        statements[stype] = {"quarters": row[1], "rows": row[2], "latest": row[3]}

    md_lines = [f"#### Cache Status for **{ticker}**", "", "| Statement | Rows | Quarters | Latest |", "|---|---|---|---|"]
    for stype, info in statements.items():
        md_lines.append(f"| {STATEMENT_TYPES.get(stype, stype)} | {info['rows']} | {info['quarters']} | `{info['latest']}` |")

    return {"ticker": ticker, "statements": statements, "markdown": "\n".join(md_lines)}


def _do_catalog(ticker: str, inst: dict) -> dict:
    """List available XBRL concepts for a ticker."""
    from .query import query_concepts
    _ensure_indexes()

    statement_type = inst.get("statement_type")
    concepts = query_concepts(ticker, statement_type)
    if not concepts:
        return {"ticker": ticker, "message": "No concepts found. Run extract first.", "concepts": []}

    st_label = f" for {STATEMENT_TYPES.get(statement_type, statement_type)}" if statement_type else ""
    md = f"#### Concept Catalog for **{ticker}**{st_label}\nFound {len(concepts)} concepts:\n\n`" + "`, `".join(concepts) + "`"
    return {"ticker": ticker, "concepts": concepts, "count": len(concepts), "markdown": md[:SUMMARY_CHAR_BUDGET]}


# ── Helpers ──────────────────────────────────────────────────────────

def _fetch_rows(ticker: str) -> List[dict]:
    conn = _get_conn()
    cur = conn.execute(
        "SELECT * FROM sf_quarterly_facts WHERE ticker=? ORDER BY statement_type, concept_order, quarter DESC",
        (ticker,)
    )
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, row)) for row in cur.fetchall()]

def _fetch_company_name(ticker: str) -> str | None:
    conn = _get_conn()
    row = conn.execute("SELECT company_name FROM sf_tickers WHERE ticker=?", (ticker,)).fetchone()
    return row[0] if row else None


def _build_extract_markdown(ticker, company_name, quarters_requested, quarters_cached, cache_hit, refresh, rows) -> str:
    action = "cache hit:" if cache_hit else ("Refreshed" if refresh else "Extracted")
    lines = [f"**{ticker}** ({company_name}) — {action} {quarters_cached} quarters.\n"]

    # Coverage table
    if rows and "statement_type" in rows[0]:
        lines.extend(["", "#### Coverage", "| Statement | Rows | Quarters | Latest |", "|---|---|---|---|"])
        seen = {}
        for r in rows:
            st = r.get("statement_type", "")
            if st not in seen: seen[st] = {"rows": 0, "quarters": set()}
            seen[st]["rows"] += 1
            seen[st]["quarters"].add(r.get("quarter", ""))
        for st in ("income", "balance", "cashflow"):
            if st not in seen: continue
            info = seen[st]
            qs = sorted(info["quarters"], reverse=True)
            lines.append(f"| {STATEMENT_TYPES.get(st, st)} | {info['rows']} | {len(qs)} | `{qs[0] if qs else '—'}` |")

    # Key metrics
    for stype in ("income", "balance", "cashflow"):
        st_rows = [r for r in rows if r.get("statement_type") == stype]
        if not st_rows: continue
        metrics = KEY_CONCEPTS.get(stype, {})
        if not metrics: continue
        all_qs = sorted({r["quarter"] for r in st_rows}, reverse=True)[:SUMMARY_QUARTERS_SHOWN]
        if not all_qs: continue
        lines.extend(["", f"#### Key {STATEMENT_TYPES.get(stype, stype)} Metrics"])
        lines.append("| Metric | " + " | ".join(f"`{q}`" for q in all_qs) + " |")
        lines.append("|---|" + "|".join(["---"] * len(all_qs)) + "|")
        for concept_full, label in metrics.items():
            c_data = [r for r in st_rows if r.get("concept") == concept_full]
            if not c_data: continue
            unit_val = c_data[0].get("unit", "USD")
            qd = {r["quarter"]: r.get("numeric_value") for r in c_data}
            row = [label] + [format_value(qd.get(q), unit_val) for q in all_qs]
            lines.append("| " + " | ".join(row) + " |")

    md = "\n".join(lines)
    return md[:SUMMARY_CHAR_BUDGET - 100] + "\n\n*[Truncated]*" if len(md) > SUMMARY_CHAR_BUDGET else md


def _build_query_markdown(ticker, statement_type, concept_filter, rows) -> str:
    lines = [f"Found **{len(rows)}** fact(s) for **{ticker}** ({STATEMENT_TYPES.get(statement_type, statement_type)})."]
    concepts = list(dict.fromkeys(r["concept"] for r in rows))
    quarters = sorted({r["quarter"] for r in rows}, reverse=True)[:8]
    lines.extend(["", "| Concept | Label | " + " | ".join(f"`{q}`" for q in quarters) + " |", "|---|---|" + "|".join(["---"] * len(quarters)) + "|"])
    idx = {(r["concept"], r["quarter"]): r for r in rows}
    for c in concepts:
        sample = next((r for r in rows if r["concept"] == c), None)
        if not sample: continue
        row = [f"`{c}`", sample.get("label", "")]
        for q in quarters:
            r = idx.get((c, q))
            row.append(format_value(r.get("numeric_value") if r else None, r.get("unit", "USD") if r else "USD"))
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


# ── Tool class ───────────────────────────────────────────────────────

class StockFinancialsTool(Tool):
    name = TOOL_NAME
    description = TOOL_DESCRIPTION
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "One of: extract, query, status, catalog",
                "enum": ["extract", "query", "status", "catalog"],
            },
            "instructions": {
                "type": "object",
                "description": "Command-specific parameters. For extract: {ticker, quarters?, refresh?}. For query: {ticker, statement_type?, concept?, start_quarter?, end_quarter?, limit?}. For status/catalog: {ticker, statement_type?}.",
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
