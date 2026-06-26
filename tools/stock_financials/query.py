# tools/stock_financials/query.py
"""Query engine for stock_financials — reads from operational DB."""
import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger("activity-server")


def _get_conn():
    from server.app import get_server
    server = get_server()
    if server is None:
        raise RuntimeError("Server not initialized")
    return server.turso


def query_facts(
    ticker: str,
    statement_type: str,
    concept: Optional[str] = None,
    start_quarter: Optional[str] = None,
    end_quarter: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    conn = _get_conn()
    where = ["ticker = ?", "statement_type = ?"]
    params: List[Any] = [ticker.upper(), statement_type.lower()]

    if concept:
        normalized = concept.replace("_", ":", 1) if concept.startswith("us-gaap_") else concept
        if normalized != concept:
            log.warning(f"StockFin:Concept:LegacyFormat — '{concept}' → '{normalized}'")
        where.append("concept = ?")
        params.append(normalized)

    if start_quarter:
        where.append("quarter >= ?")
        params.append(start_quarter)
    if end_quarter:
        where.append("quarter <= ?")
        params.append(end_quarter)

    sql = f"SELECT * FROM sf_quarterly_facts WHERE {' AND '.join(where)} ORDER BY concept_order ASC, quarter DESC LIMIT ?"
    params.append(limit)
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def query_concepts(ticker: str, statement_type: Optional[str] = None) -> List[str]:
    conn = _get_conn()
    where = ["ticker = ?"]
    params = [ticker.upper()]
    if statement_type:
        where.append("statement_type = ?")
        params.append(statement_type.lower())
    sql = f"SELECT DISTINCT concept FROM sf_quarterly_facts WHERE {' AND '.join(where)} ORDER BY concept"
    return [r[0] for r in conn.execute(sql, params).fetchall()]
