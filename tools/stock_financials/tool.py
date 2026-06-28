# tools/stock_financials/tool.py
"""Stock Financials Tool — SEC EDGAR quarterly fact extraction.

Activities:
  1. stock_financials.validate — Parse and validate command + instructions
  2. stock_financials.execute  — Run the appropriate sub-command (extract/query/status/catalog)
  3. stock_financials.format   — Normalize result into response dict
"""
import json
import sqlite3
from typing import Any, Dict, List

from tools import Tool
from server.accumulator import Activity
from .config import TOOL_NAME
from .docs import TOOL_DESCRIPTION, TOOL_DOCS, TOOL_OUTPUT_EXAMPLE


# ── Presentation constants ───────────────────────────────────────────

STATEMENT_TYPES = {
    "income": "Income Statement",
    "balance": "Balance Sheet",
    "cashflow": "Cash Flow Statement",
}
PER_SHARE_UNITS = frozenset({
    "USD per share", "USD/shares", "TWD per share",
    "JPY per share", "EUR per share", "GBP per share",
})
SHARE_UNITS = frozenset({"shares"})
_CURRENCY_SYMBOLS = {
    "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "TWD": "NT$",
    "CNY": "¥", "KRW": "₩", "INR": "₹", "AUD": "A$", "CAD": "C$",
    "CHF": "CHF", "SGD": "S$", "HKD": "HK$",
}
# Key concepts for summary display are derived dynamically from the blueprint.
# See _build_key_metrics() which uses is_total=True rows from the blueprint.
SUMMARY_QUARTERS_SHOWN = 4


def _extract_currency_code(unit: str) -> str | None:
    if not unit:
        return None
    first = unit.strip().split()[0].split("/")[0].upper()
    return None if first in {"SHARES", "PURE", ""} else first


def _currency_symbol(code: str | None) -> str:
    if not code:
        return "$"
    return _CURRENCY_SYMBOLS.get(code, code)


def format_value(value, unit: str = "USD") -> str | None:
    """Format a numeric value for display. Returns None if value is None."""
    if value is None:
        return None
    try:
        val = float(value)
    except (TypeError, ValueError):
        return str(value)
    code = _extract_currency_code(unit)
    sym = _currency_symbol(code)
    if unit in PER_SHARE_UNITS:
        return f"{sym}{val:,.2f}" if abs(val) >= 0.01 else f"{sym}{val:.4f}"
    if unit in SHARE_UNITS:
        if abs(val) >= 1e9:
            return f"{val / 1e9:,.2f}B"
        if abs(val) >= 1e6:
            return f"{val / 1e6:,.2f}M"
        return f"{val:,.0f}"
    if abs(val) >= 1e9:
        return f"{sym}{val / 1e9:,.1f}B"
    if abs(val) >= 1e6:
        return f"{sym}{val / 1e6:,.1f}M"
    return f"{sym}{val:,.0f}"


def _get_conn():
    """Get a turso connection to the operational database."""
    from server.app import get_server
    server = get_server()
    if server is None:
        raise RuntimeError("Server not initialized")
    return server.turso


def _get_dual_writer():
    """Get the dual writer for cloud-synced operations."""
    from server.app import get_server
    server = get_server()
    if server is None:
        raise RuntimeError("Server not initialized")
    return server.dual_writer


def _ensure_indexes():
    """Create composite unique index for sf_quarterly_facts if not exists."""
    conn = _get_conn()
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_sf_quarterly_facts_key
                    ON sf_quarterly_facts(ticker, statement_type, concept, quarter)""")
    conn.commit()


# ── Activities ───────────────────────────────────────────────────────

@Activity("stock_financials.validate")
def validate(acc, arguments: dict) -> dict:
    """Parse and validate command + instructions.

    Normalizes ticker to uppercase, parses JSON string instructions,
    validates the command, and validates statement_type if present.
    """
    command = arguments.get("command", "").strip().lower()
    if command not in ("extract", "query", "status", "catalog"):
        raise ValueError(
            f"Invalid command '{command}'. Use: extract, query, status, catalog"
        )
    instructions = arguments.get("instructions", {})
    if isinstance(instructions, str):
        try:
            instructions = json.loads(instructions)
        except Exception:
            raise ValueError("Instructions must be a valid JSON object")
    ticker = (instructions.get("ticker") or "").upper().strip()
    if not ticker and command != "catalog":
        raise ValueError("Missing required field: ticker")

    # Validate statement_type early for query and catalog commands
    st = instructions.get("statement_type")
    if st and st not in STATEMENT_TYPES:
        raise ValueError(
            f"Invalid statement_type '{st}'. Use: income, balance, cashflow"
        )

    return {"command": command, "ticker": ticker, "instructions": instructions}


@Activity("stock_financials.execute")
def execute_command(acc, validated: dict) -> Any:
    """Dispatch to the appropriate sub-command handler.

    Routes to: _do_extract, _do_query, _do_status, or _do_catalog.
    """
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
    """Normalize the result into a response dict.

    Ensures consistent JSON structure. Passes through error dicts unchanged.
    """
    if isinstance(result, dict) and "error" in result:
        return result
    return result


# ── Sub-command implementations ──────────────────────────────────────

def _do_extract(ticker: str, inst: dict) -> dict:
    """Extract quarterly financials from EDGAR and persist to DB.

    Cache-first: if enough quarters are cached and refresh is false, skips EDGAR.
    On refresh: deletes local + cloud data, re-fetches from EDGAR.
    """
    from .extractor import extract_and_persist

    quarters = min(int(inst.get("quarters", 8)), 40)
    refresh = bool(inst.get("refresh", False))
    _ensure_indexes()

    conn = _get_conn()
    existing = conn.execute(
        "SELECT COUNT(DISTINCT quarter) FROM sf_quarterly_facts WHERE ticker=?",
        (ticker,),
    ).fetchone()
    cache_hit = existing[0] >= quarters and not refresh

    if not cache_hit:
        result = extract_and_persist(ticker, quarters, refresh)
    else:
        result = {
            "ticker": ticker,
            "company": ticker,
            "persisted": {},
            "cache_hit": True,
        }

    # Fetch all rows for summary
    rows = _fetch_rows(ticker)
    if not rows:
        return {"ticker": ticker, "message": "No data extracted", "rows": 0}

    company_name = _fetch_company_name(ticker) or ticker
    quarters_found = len({r["quarter"] for r in rows})

    # Build structured coverage
    coverage = _build_coverage(rows)

    # Build key metrics per statement type
    key_metrics = _build_key_metrics(rows)

    return {
        "ticker": ticker,
        "company": company_name,
        "quarters_requested": quarters,
        "quarters_cached": quarters_found,
        "cache_hit": cache_hit,
        "refresh": refresh,
        "total_rows": len(rows),
        "persisted": result.get("persisted", {}),
        "coverage": coverage,
        "key_metrics": key_metrics,
    }


def _do_query(ticker: str, inst: dict) -> dict:
    """Query cached financial facts from the operational database.

    Supports filtering by statement_type (income/balance/cashflow),
    specific XBRL concept, quarter range, and row limit.
    """
    from .query import query_facts

    _ensure_indexes()

    statement_type = inst.get("statement_type", "income")
    if statement_type not in STATEMENT_TYPES:
        return {
            "error": f"Invalid statement_type '{statement_type}'. Use: income, balance, cashflow"
        }

    concept = inst.get("concept")
    start_q = inst.get("start_quarter")
    end_q = inst.get("end_quarter")
    limit = min(int(inst.get("limit", 100)), 500)

    records = query_facts(ticker, statement_type, concept, start_q, end_q, limit)
    if not records:
        return {
            "ticker": ticker,
            "statement_type": statement_type,
            "records": 0,
            "message": "No records found. Run extract first.",
        }

    # Build pivot table: concepts × quarters
    concepts = list(dict.fromkeys(r["concept"] for r in records))
    quarters = sorted({r["quarter"] for r in records}, reverse=True)[:8]

    pivot = {}
    idx = {(r["concept"], r["quarter"]): r for r in records}
    for c in concepts:
        sample = next((r for r in records if r["concept"] == c), None)
        if not sample:
            continue
        row_data = {"label": sample.get("label", ""), "concept": c}
        for q in quarters:
            r = idx.get((c, q))
            raw_val = r.get("numeric_value") if r else None
            unit = r.get("unit", "USD") if r else "USD"
            row_data[q] = {
                "raw": raw_val,
                "formatted": format_value(raw_val, unit),
                "unit": unit,
            }
        pivot[c] = row_data

    return {
        "ticker": ticker,
        "statement_type": statement_type,
        "statement_label": STATEMENT_TYPES.get(statement_type, statement_type),
        "records": len(records),
        "concepts": concepts,
        "quarters": quarters,
        "data": pivot,
    }


def _do_status(ticker: str) -> dict:
    """Check cache status for a ticker.

    Returns per-statement-type counts of rows, quarters, and latest quarter.
    """
    _ensure_indexes()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT statement_type, COUNT(DISTINCT quarter) as q_count, "
        "COUNT(*) as r_count, MAX(quarter) as latest "
        "FROM sf_quarterly_facts WHERE ticker=? GROUP BY statement_type",
        (ticker,),
    ).fetchall()

    if not rows:
        return {
            "ticker": ticker,
            "message": "No cached data. Run extract first.",
            "statements": {},
        }

    statements = {}
    for row in rows:
        stype = row[0]
        statements[stype] = {
            "label": STATEMENT_TYPES.get(stype, stype),
            "rows": row[2],
            "quarters": row[1],
            "latest_quarter": row[3],
        }

    return {"ticker": ticker, "statements": statements}


def _do_catalog(ticker: str, inst: dict) -> dict:
    """List available XBRL concepts for a ticker.

    Optionally filtered by statement_type (income/balance/cashflow).
    """
    from .query import query_concepts

    _ensure_indexes()

    statement_type = inst.get("statement_type")
    if statement_type and statement_type not in STATEMENT_TYPES:
        return {
            "error": f"Invalid statement_type '{statement_type}'. Use: income, balance, cashflow"
        }

    concepts = query_concepts(ticker, statement_type)
    if not concepts:
        return {
            "ticker": ticker,
            "message": "No concepts found. Run extract first.",
            "concepts": [],
        }

    return {
        "ticker": ticker,
        "statement_filter": statement_type,
        "concepts": concepts,
        "count": len(concepts),
    }


# ── Helpers ──────────────────────────────────────────────────────────


def _fetch_rows(ticker: str) -> List[dict]:
    conn = _get_conn()
    cur = conn.execute(
        "SELECT * FROM sf_quarterly_facts WHERE ticker=? "
        "ORDER BY statement_type, concept_order, quarter DESC",
        (ticker,),
    )
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _fetch_company_name(ticker: str) -> str | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT company_name FROM sf_tickers WHERE ticker=?", (ticker,)
    ).fetchone()
    return row[0] if row else None


def _build_coverage(rows: List[dict]) -> dict:
    """Build coverage summary per statement type."""
    seen = {}
    for r in rows:
        st = r.get("statement_type", "")
        if st not in seen:
            seen[st] = {"rows": 0, "quarters": set()}
        seen[st]["rows"] += 1
        seen[st]["quarters"].add(r.get("quarter", ""))

    coverage = {}
    for st in ("income", "balance", "cashflow"):
        if st not in seen:
            continue
        info = seen[st]
        qs = sorted(info["quarters"], reverse=True)
        coverage[st] = {
            "label": STATEMENT_TYPES.get(st, st),
            "rows": info["rows"],
            "quarter_count": len(qs),
            "latest_quarter": qs[0] if qs else None,
        }
    return coverage


def _build_key_metrics(rows: List[dict]) -> dict:
    """Build key metrics pivot per statement type.

    Dynamically selects key metrics from rows where is_total=True
    (totals/subtotals from the statement blueprint). No hardcoded concept lists.
    """
    key_metrics = {}
    for stype in ("income", "balance", "cashflow"):
        st_rows = [r for r in rows if r.get("statement_type") == stype]
        if not st_rows:
            continue
        all_qs = sorted({r["quarter"] for r in st_rows}, reverse=True)[
            :SUMMARY_QUARTERS_SHOWN
        ]
        if not all_qs:
            continue

        # Use is_total rows from the blueprint as key metrics
        total_rows = [r for r in st_rows if r.get("is_total")]
        if not total_rows:
            # Fallback: use rows with depth <= 1 (top-level items)
            total_rows = [r for r in st_rows if r.get("depth", 0) <= 1]

        # Get unique concepts, preserving order
        seen_concepts = []
        for r in total_rows:
            c = r.get("concept")
            if c and c not in seen_concepts:
                seen_concepts.append(c)

        type_metrics = {}
        for concept_full in seen_concepts:
            c_data = [r for r in st_rows if r.get("concept") == concept_full and r["quarter"] in all_qs]
            if not c_data:
                continue
            label = c_data[0].get("label", concept_full.split(":")[-1])
            unit_val = c_data[0].get("unit", "USD")
            qd = {r["quarter"]: r.get("numeric_value") for r in c_data}
            type_metrics[concept_full] = {
                "label": label,
                "unit": unit_val,
                "quarters": {
                    q: {
                        "raw": qd.get(q),
                        "formatted": format_value(qd.get(q), unit_val),
                    }
                    for q in all_qs
                },
            }
        key_metrics[stype] = {
            "label": STATEMENT_TYPES.get(stype, stype),
            "quarters_shown": all_qs,
            "metrics": type_metrics,
        }
    return key_metrics


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
                "description": (
                    "Command-specific parameters. "
                    "For extract: {ticker, quarters?, refresh?}. "
                    "For query: {ticker, statement_type?, concept?, "
                    "start_quarter?, end_quarter?, limit?}. "
                    "For status/catalog: {ticker, statement_type?}."
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
