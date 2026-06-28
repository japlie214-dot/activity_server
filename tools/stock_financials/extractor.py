# tools/stock_financials/extractor.py
"""Stock Financials Extractor — fetches XBRL facts from SEC EDGAR and persists to DB.

Uses edgartools for EDGAR access, pandas for data processing, and the project's
turso connection for database operations.

Design principles:
  - No hardcoded concept lists, statement shapes, or field names
  - Use edgartools' own metadata (blueprint, fiscal_period, period_type)
  - Q4 is always derived from FY - (Q1+Q2+Q3) for income/cashflow statements
  - All writes go through DualWriter — never raw conn.execute() on synced tables
"""
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import pandas as pd

log = logging.getLogger("activity-server")

MAX_QUARTERLY_DURATION_DAYS = 105


def _get_conn():
    from server.app import get_server
    server = get_server()
    if server is None:
        raise RuntimeError("Server not initialized")
    return server.turso


def _get_dual_writer():
    from server.app import get_server
    server = get_server()
    if server is None:
        raise RuntimeError("Server not initialized")
    return server.dual_writer


def _parse_quarter_label(qlabel: str) -> Tuple[int, int]:
    try:
        parts = qlabel.split("-Q")
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return 0, 0


def _get_quarter_date_map(df: pd.DataFrame) -> Dict[str, pd.Timestamp]:
    date_map = {}
    if df.empty or "quarter_label" not in df.columns or "period_end" not in df.columns:
        return date_map
    for ql in df["quarter_label"].unique():
        subset = df[df["quarter_label"] == ql]
        dates = pd.to_datetime(subset["period_end"], errors="coerce").dropna()
        if len(dates) > 0:
            date_map[ql] = dates.median()
    return date_map


def _sort_quarters_desc(quarter_labels: List[str], date_map=None) -> List[str]:
    if date_map:
        return sorted(quarter_labels, key=lambda q: date_map.get(q, pd.Timestamp.min), reverse=True)
    return sorted(quarter_labels, key=_parse_quarter_label, reverse=True)


def _period_end_to_quarter_label(period_end, fiscal_period: str) -> str:
    if not period_end:
        return "Unknown"
    try:
        dt = pd.Timestamp(period_end)
        year = dt.year
        if fiscal_period == "Q4" and dt.month <= 3:
            year -= 1
        return f"{year}-{fiscal_period}"
    except Exception:
        return f"{period_end}-{fiscal_period}"


def compute_fact_hash(record: dict) -> str:
    parts = [str(record.get(k, "")) for k in ("ticker", "statement_type", "concept", "quarter", "numeric_value")]
    return hashlib.md5("||".join(parts).encode("utf-8", errors="replace")).hexdigest()


def _is_non_additive_concept(concept: str) -> bool:
    """Check if a concept is non-additive (per-share, share counts, etc.).

    Uses pattern matching on the concept name rather than a hardcoded list.
    """
    patterns = [
        r"EarningsPerShare",
        r"WeightedAverageNumberOf.*Shares",
        r"CommonStockDividendsPerShare",
        r"AntidilutiveSecurities",
        r"PerShare",
        r"NumberOfShares",
    ]
    return any(__import__("re").search(p, concept, __import__("re").IGNORECASE) for p in patterns)


def get_statement_blueprint(facts, statement_type: str) -> pd.DataFrame:
    """Get the statement blueprint dynamically from edgartools.

    Uses edgartools' own Statement object → to_dataframe() to get the
    canonical structure including labels, depth, abstract flags, and ordering.
    No hardcoded concept lists.
    """
    methods = {
        "income": "income_statement",
        "balance": "balance_sheet",
        "cashflow": "cashflow_statement",
    }
    try:
        stmt_obj = getattr(facts, methods[statement_type])()
        if stmt_obj is None:
            return pd.DataFrame()
        df = stmt_obj.to_dataframe()
        if df.empty:
            return df

        rows = []
        for idx, r in df.iterrows():
            concept_short = str(idx)
            rows.append({
                "concept": f"us-gaap:{concept_short}",
                "label": str(r.get("label", concept_short)),
                "depth": int(r.get("depth", 0)),
                "is_abstract": bool(r.get("is_abstract", False)),
                "is_total": bool(r.get("is_total", False)),
            })
        return pd.DataFrame(rows)
    except Exception as e:
        log.warning(f"StockFin:Blueprint:Error — {e}")
        return pd.DataFrame()


def _enrich_with_blueprint(df: pd.DataFrame, blueprint: pd.DataFrame) -> pd.DataFrame:
    """Enrich extracted data with labels, depth, ordering from the blueprint."""
    if blueprint.empty:
        return df
    label_map = dict(zip(blueprint["concept"], blueprint["label"]))
    depth_map = dict(zip(blueprint["concept"], blueprint["depth"]))
    total_map = dict(zip(blueprint["concept"], blueprint["is_total"]))
    order_map = {c: i for i, c in enumerate(blueprint["concept"])}
    df["label"] = df["concept"].map(label_map).fillna(df["concept"].apply(lambda c: c.split(":")[-1]))
    df["depth"] = df["concept"].map(depth_map).fillna(0).astype(int)
    df["is_total"] = df["concept"].map(total_map).fillna(False).astype(bool).astype(int)
    df["concept_order"] = df["concept"].map(order_map).fillna(999).astype(int)
    return df


def _limit_quarters(df: pd.DataFrame, num_quarters: int) -> pd.DataFrame:
    if df.empty or num_quarters <= 0:
        return df
    all_q = _sort_quarters_desc(df["quarter_label"].unique().tolist(), date_map=_get_quarter_date_map(df))
    return df[df["quarter_label"].isin(all_q[:num_quarters])].reset_index(drop=True)


def _deduplicate_facts(df: pd.DataFrame) -> pd.DataFrame:
    """Remove duplicate XBRL facts for the same concept+period.

    XBRL filings often report the same fact multiple times (e.g., different
    dimensions, restatements). Keep the most recent/definitive value.
    """
    if df.empty:
        return df
    # Sort to prioritize: longer duration first (full-year over partial), then latest period_end
    sort_cols = ["concept", "period_end", "fiscal_period"]
    ascending = [True, True, True]
    if "calc_duration" in df.columns:
        sort_cols.insert(2, "calc_duration")
        ascending.insert(2, False)
    df = df.sort_values(sort_cols, ascending=ascending)
    # For same concept+period_end+fiscal_period, keep first (longest duration)
    df = df.drop_duplicates(subset=["concept", "period_end", "fiscal_period"], keep="first")
    return df


def _derive_q4_from_fy(all_facts_df, concepts, existing_df, statement_type):
    """Derive Q4 values by subtracting YTD Q1-Q3 from FY.

    10-K filings report FY (full year) but not Q4 explicitly in XBRL.
    For non-additive concepts (EPS, share counts), Q4 cannot be derived
    by subtraction — those are skipped.
    """
    additive = [c for c in concepts if not _is_non_additive_concept(c)]
    if not additive:
        return existing_df

    # Get FY data (duration period_type)
    fy_data = all_facts_df[
        (all_facts_df["concept"].isin(additive))
        & (all_facts_df["fiscal_period"] == "FY")
        & (all_facts_df["period_type"] == "duration")
    ].copy()

    # Get cumulative YTD Q3 data (the 10-K's quarterly breakdowns)
    ytd_q3 = all_facts_df[
        (all_facts_df["concept"].isin(additive))
        & (all_facts_df["fiscal_period"] == "Q3")
        & (all_facts_df["period_type"] == "duration")
    ].copy()

    if fy_data.empty:
        return existing_df

    # Calculate durations for deduplication
    for d in [fy_data, ytd_q3]:
        if d.empty:
            continue
        d["period_start_dt"] = pd.to_datetime(d["period_start"], errors="coerce")
        d["period_end_dt"] = pd.to_datetime(d["period_end"], errors="coerce")
        d["calc_duration"] = (d["period_end_dt"] - d["period_start_dt"]).dt.days

    # Deduplicate: for same concept+period_start, keep longest duration
    if not ytd_q3.empty:
        ytd_q3 = (
            ytd_q3.sort_values(["concept", "period_start", "calc_duration"], ascending=[True, True, False])
            .drop_duplicates(["concept", "period_start"], keep="first")
        )
    fy_data = (
        fy_data.sort_values(["concept", "period_start", "calc_duration"], ascending=[True, True, False])
        .drop_duplicates(["concept", "period_start"], keep="first")
    )

    derived = []
    for _, fy in fy_data.iterrows():
        c, ps, fv, pe, fy_yr, unit = (
            fy["concept"],
            fy["period_start"],
            fy["numeric_value"],
            fy["period_end"],
            fy["fiscal_year"],
            fy.get("unit", "USD"),
        )
        if pd.isna(fv):
            continue
        # Skip if Q4 already exists
        if len(existing_df[(existing_df["concept"] == c) & (existing_df["fiscal_period"] == "Q4") & (existing_df["period_end"] == pe)]) > 0:
            continue
        # Find matching YTD Q3
        mq3 = ytd_q3[(ytd_q3["concept"] == c) & (ytd_q3["period_start"] == ps)] if not ytd_q3.empty else pd.DataFrame()
        if not mq3.empty and not pd.isna(mq3["numeric_value"].iloc[0]):
            derived.append({
                "concept": c,
                "period_end": pe,
                "fiscal_period": "Q4",
                "fiscal_year": fy_yr,
                "quarter_label": _period_end_to_quarter_label(pe, "Q4"),
                "numeric_value": fv - mq3["numeric_value"].iloc[0],
                "unit": unit,
                "period_type": "duration",
                "statement_type": statement_type,
            })

    if derived:
        res = pd.concat([existing_df, pd.DataFrame(derived)], ignore_index=True).dropna(subset=["numeric_value"])
        return res.sort_values(["concept", "quarter_label", "fiscal_year"], ascending=[True, True, False]).drop_duplicates(
            ["concept", "quarter_label"], keep="first"
        )
    return existing_df


def _process_duration_statement(all_facts_df, bp, num_quarters, statement_type):
    """Generic processor for duration-based statements (income, cashflow).

    Uses the blueprint to determine which concepts to extract, filters by
    standard quarterly periods, and derives Q4 from FY.
    """
    data_c = bp[~bp["is_abstract"]]["concept"].tolist()
    c_list = [c for c in data_c if c in set(all_facts_df["concept"].unique())]
    if not c_list:
        return pd.DataFrame()

    df = all_facts_df[
        (all_facts_df["concept"].isin(c_list))
        & (all_facts_df["fiscal_period"].isin({"Q1", "Q2", "Q3", "Q4"}))
        & (all_facts_df["period_type"] == "duration")
    ].copy()

    if df.empty:
        return pd.DataFrame()

    # Calculate duration for filtering and deduplication
    df["period_start_dt"] = pd.to_datetime(df["period_start"], errors="coerce")
    df["period_end_dt"] = pd.to_datetime(df["period_end"], errors="coerce")
    df["calc_duration"] = (df["period_end_dt"] - df["period_start_dt"]).dt.days

    # Filter out unreasonably long periods (likely cumulative YTD mislabeled as quarterly)
    df = df[df["calc_duration"] <= MAX_QUARTERLY_DURATION_DAYS]

    # Deduplicate: same concept+period_end+fiscal_period → keep first
    df = (
        df.sort_values(["concept", "period_end", "fiscal_period", "fiscal_year"], ascending=[True, True, True, False])
        .drop_duplicates(["concept", "period_end", "fiscal_period"], keep="first")
    )

    df["quarter_label"] = df.apply(lambda r: _period_end_to_quarter_label(r["period_end"], r["fiscal_period"]), axis=1)

    # Derive Q4 from FY for income and cashflow statements
    df = _derive_q4_from_fy(all_facts_df, c_list, df, statement_type)

    if df.empty:
        return df
    df = _enrich_with_blueprint(df, bp)
    df["statement_type"] = statement_type
    return _limit_quarters(df, num_quarters)


def _process_instant_statement(all_facts_df, bp, num_quarters, statement_type):
    """Generic processor for instant-based statements (balance sheet).

    Balance sheet uses instant period_type. FY values are remapped to Q4
    since 10-K filings report the year-end balance sheet as FY.
    """
    data_c = bp[~bp["is_abstract"]]["concept"].tolist()
    c_list = [c for c in data_c if c in set(all_facts_df["concept"].unique())]
    if not c_list:
        return pd.DataFrame()

    df = all_facts_df[
        (all_facts_df["concept"].isin(c_list))
        & (all_facts_df["fiscal_period"].isin({"Q1", "Q2", "Q3", "Q4", "FY"}))
        & (all_facts_df["period_type"] == "instant")
    ].copy()

    if df.empty:
        return df

    # Deduplicate: same concept+period_end → keep first
    df = df.sort_values(["concept", "period_end", "fiscal_period"], ascending=[True, True, True]).drop_duplicates(
        ["concept", "period_end"], keep="first"
    )

    # Remap FY → Q4 (10-K reports year-end balance as FY, not Q4)
    df.loc[df["fiscal_period"] == "FY", "fiscal_period"] = "Q4"

    df = _enrich_with_blueprint(df, bp)
    df["quarter_label"] = df.apply(lambda r: _period_end_to_quarter_label(r["period_end"], r["fiscal_period"]), axis=1)
    df["statement_type"] = statement_type
    return _limit_quarters(df, num_quarters)


def upsert_quarterly_records(records: List[Dict[str, Any]]) -> int:
    """Insert or update quarterly fact records in operational + cloud databases.

    All writes go through DualWriter. Direct conn.execute() on synced tables
    is forbidden.
    """
    if not records:
        return 0
    now_iso = datetime.now(timezone.utc).isoformat()

    columns = [
        "ticker", "statement_type", "concept", "label", "quarter", "period_end",
        "fiscal_period", "fiscal_year", "numeric_value", "unit", "period_type",
        "depth", "is_total", "concept_order", "content_hash",
        "extracted_at", "created_at", "updated_at",
    ]

    dw = _get_dual_writer()

    for r in records:
        r["quarter"] = r.get("quarter_label", "")
        nv = r.get("numeric_value")
        r["numeric_value"] = None if pd.isna(nv) else nv
        r["content_hash"] = compute_fact_hash(r)
        r["extracted_at"] = now_iso
        r["created_at"] = now_iso
        r["updated_at"] = now_iso

        # Sanitize all values for SQLite — convert pandas types to native Python
        for key, val in list(r.items()):
            if val is None:
                continue
            if isinstance(val, float) and pd.isna(val):
                r[key] = None
            elif hasattr(val, "isoformat"):
                r[key] = str(val)
            elif isinstance(val, int) and not isinstance(val, bool):
                r[key] = int(val)
            elif isinstance(val, float):
                r[key] = float(val)
            elif not isinstance(val, (str, bytes)):
                r[key] = str(val)

        defaults = {"fiscal_year": 0, "depth": 0, "is_total": 0, "concept_order": 0}
        data = {c: r.get(c, defaults.get(c, "")) for c in columns}

        if dw:
            dw.upsert("sf_quarterly_facts", data)
        else:
            # Fallback only when DualWriter is not available (e.g., testing)
            conn = _get_conn()
            cols_sql = ", ".join(data.keys())
            placeholders = ", ".join(["?"] * len(data))
            conn.execute(
                f"INSERT OR REPLACE INTO sf_quarterly_facts ({cols_sql}) VALUES ({placeholders})",
                list(data.values()),
            )
            conn.commit()

    return len(records)


def extract_and_persist(ticker: str, num_quarters: int, refresh: bool) -> Dict[str, Any]:
    """Main extraction entry point. Fetches from EDGAR and persists to DB.

    Uses edgartools dynamically:
      - company.get_facts() → all XBRL facts as DataFrame
      - facts.income_statement/balance_sheet/cashflow_statement → blueprint
      - No hardcoded concept lists or statement shapes
    """
    ticker = ticker.upper()

    conn = _get_conn()
    dw = _get_dual_writer()

    if refresh:
        if dw:
            dw.delete("sf_quarterly_facts", "WHERE ticker = ?", (ticker,))
        else:
            conn.execute("DELETE FROM sf_quarterly_facts WHERE ticker = ?", (ticker,))
            conn.commit()

    # Check if we already have this ticker's metadata
    existing_ticker = conn.execute(
        "SELECT company_name, cik FROM sf_tickers WHERE ticker=?", (ticker,)
    ).fetchone()

    from edgar import set_identity, Company
    import os
    identity = os.environ.get("EDGAR_IDENTITY", "analyst@research.com")
    set_identity(identity)

    company = Company(ticker)
    now_iso = datetime.now(timezone.utc).isoformat()

    # Persist ticker metadata (skip if already exists and not refreshing)
    if not existing_ticker or refresh:
        ticker_data = {
            "ticker": ticker,
            "company_name": company.name,
            "cik": str(company.cik),
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        if dw:
            dw.upsert("sf_tickers", ticker_data)
        else:
            conn.execute(
                "INSERT OR IGNORE INTO sf_tickers (ticker, company_name, cik, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (ticker, company.name, str(company.cik), now_iso, now_iso),
            )
            conn.commit()

    facts = company.get_facts()
    all_facts_df = facts.to_dataframe()

    # Build blueprints dynamically from edgartools
    blueprints = {s: get_statement_blueprint(facts, s) for s in ["income", "balance", "cashflow"]}

    # Process each statement type using generic processors
    processors = {
        "income": lambda bp, nq: _process_duration_statement(all_facts_df, bp, nq, "income"),
        "balance": lambda bp, nq: _process_instant_statement(all_facts_df, bp, nq, "balance"),
        "cashflow": lambda bp, nq: _process_duration_statement(all_facts_df, bp, nq, "cashflow"),
    }
    results = {}

    for stype, func in processors.items():
        try:
            df = func(blueprints[stype], num_quarters)
            if not df.empty:
                df["ticker"] = ticker
                records = df.to_dict(orient="records")
                count = upsert_quarterly_records(records)
                results[stype] = count
            else:
                results[stype] = 0
        except Exception as e:
            log.error(f"StockFin:Extract:{stype} — {e}")
            results[stype] = 0

    return {"ticker": ticker, "company": company.name, "persisted": results}
