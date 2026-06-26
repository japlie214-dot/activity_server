# tools/stock_financials/extractor.py
"""Stock Financials Extractor — fetches XBRL facts from SEC EDGAR and persists to DB.

Uses edgartools for EDGAR access, pandas for data processing, and the project's
turso connection for database operations.
"""
import hashlib
import logging
import re
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
    if not period_end: return "Unknown"
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
    patterns = [r"EarningsPerShare", r"WeightedAverageNumberOf.*Shares", r"CommonStockDividendsPerShare", r"AntidilutiveSecurities"]
    return any(re.search(p, concept) for p in patterns)


def get_statement_blueprint(facts, statement_type: str) -> pd.DataFrame:
    methods = {"income": "income_statement", "balance": "balance_sheet", "cashflow": "cashflow_statement"}
    try:
        stmt_obj = getattr(facts, methods[statement_type])()
        if stmt_obj is None: return pd.DataFrame()
        df = stmt_obj.to_dataframe()
        if df.empty: return df
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
    if df.empty or num_quarters <= 0: return df
    all_q = _sort_quarters_desc(df["quarter_label"].unique().tolist(), date_map=_get_quarter_date_map(df))
    return df[df["quarter_label"].isin(all_q[:num_quarters])].reset_index(drop=True)


def _derive_q4_from_fy(all_facts_df, concepts, existing_df, statement_type):
    additive = [c for c in concepts if not _is_non_additive_concept(c)]
    if not additive: return existing_df
    fy_data = all_facts_df[(all_facts_df["concept"].isin(additive)) & (all_facts_df["fiscal_period"] == "FY") & (all_facts_df["period_type"] == "duration")].copy()
    ytd_q3 = all_facts_df[(all_facts_df["concept"].isin(additive)) & (all_facts_df["fiscal_period"] == "Q3") & (all_facts_df["period_type"] == "duration")].copy()
    if fy_data.empty: return existing_df
    for d in [fy_data, ytd_q3]:
        if d.empty: continue
        d["period_start_dt"] = pd.to_datetime(d["period_start"], errors="coerce")
        d["period_end_dt"] = pd.to_datetime(d["period_end"], errors="coerce")
        d["calc_duration"] = (d["period_end_dt"] - d["period_start_dt"]).dt.days
    ytd_q3 = ytd_q3.sort_values(["concept", "period_start", "calc_duration"], ascending=[True, True, False]).drop_duplicates(["concept", "period_start"], keep="first") if not ytd_q3.empty else ytd_q3
    fy_data = fy_data.sort_values(["concept", "period_start", "calc_duration"], ascending=[True, True, False]).drop_duplicates(["concept", "period_start"], keep="first")
    derived = []
    for _, fy in fy_data.iterrows():
        c, ps, fv, pe, fy_yr, unit = fy["concept"], fy["period_start"], fy["numeric_value"], fy["period_end"], fy["fiscal_year"], fy.get("unit", "USD")
        if pd.isna(fv): continue
        if len(existing_df[(existing_df["concept"] == c) & (existing_df["fiscal_period"] == "Q4") & (existing_df["period_end"] == pe)]) > 0: continue
        mq3 = ytd_q3[(ytd_q3["concept"] == c) & (ytd_q3["period_start"] == ps)] if not ytd_q3.empty else pd.DataFrame()
        if not mq3.empty and not pd.isna(mq3["numeric_value"].iloc[0]):
            derived.append({"concept": c, "period_end": pe, "fiscal_period": "Q4", "fiscal_year": fy_yr, "quarter_label": _period_end_to_quarter_label(pe, "Q4"), "numeric_value": fv - mq3["numeric_value"].iloc[0], "unit": unit, "period_type": "duration", "statement_type": statement_type})
    if derived:
        res = pd.concat([existing_df, pd.DataFrame(derived)], ignore_index=True).dropna(subset=["numeric_value"])
        return res.sort_values(["concept", "quarter_label", "fiscal_year"], ascending=[True, True, False]).drop_duplicates(["concept", "quarter_label"], keep="first")
    return existing_df


def process_income(all_facts_df, bp, num_quarters):
    data_c = bp[~bp["is_abstract"]]["concept"].tolist()
    c_list = [c for c in data_c if c in set(all_facts_df["concept"].unique())]
    if not c_list: return pd.DataFrame()
    df = all_facts_df[(all_facts_df["concept"].isin(c_list)) & (all_facts_df["fiscal_period"].isin({"Q1","Q2","Q3","Q4"})) & (all_facts_df["period_type"] == "duration")].copy()
    if not df.empty:
        df["period_start_dt"] = pd.to_datetime(df["period_start"], errors="coerce")
        df["period_end_dt"] = pd.to_datetime(df["period_end"], errors="coerce")
        df["calc_duration"] = (df["period_end_dt"] - df["period_start_dt"]).dt.days
        df = df[df["calc_duration"] <= MAX_QUARTERLY_DURATION_DAYS]
        df = df.sort_values(["concept", "period_end", "fiscal_period", "fiscal_year"], ascending=[True, True, True, False]).drop_duplicates(["concept", "period_end", "fiscal_period"], keep="first")
    else:
        return pd.DataFrame()
    df["quarter_label"] = df.apply(lambda r: _period_end_to_quarter_label(r["period_end"], r["fiscal_period"]), axis=1)
    df = _derive_q4_from_fy(all_facts_df, c_list, df, "income")
    if df.empty: return df
    df = _enrich_with_blueprint(df, bp)
    df["statement_type"] = "income"
    return _limit_quarters(df, num_quarters)


def process_balance(all_facts_df, bp, num_quarters):
    data_c = bp[~bp["is_abstract"]]["concept"].tolist()
    c_list = [c for c in data_c if c in set(all_facts_df["concept"].unique())]
    if not c_list: return pd.DataFrame()
    df = all_facts_df[(all_facts_df["concept"].isin(c_list)) & (all_facts_df["fiscal_period"].isin({"Q1","Q2","Q3","Q4","FY"})) & (all_facts_df["period_type"] == "instant")].copy()
    if df.empty: return df
    df = df.sort_values(["concept", "period_end", "fiscal_period"], ascending=[True, True, True]).drop_duplicates(["concept", "period_end"], keep="first")
    df.loc[df["fiscal_period"] == "FY", "fiscal_period"] = "Q4"
    df = _enrich_with_blueprint(df, bp)
    df["quarter_label"] = df.apply(lambda r: _period_end_to_quarter_label(r["period_end"], r["fiscal_period"]), axis=1)
    df["statement_type"] = "balance"
    return _limit_quarters(df, num_quarters)


def process_cashflow(all_facts_df, bp, num_quarters):
    data_c = bp[~bp["is_abstract"]]["concept"].tolist()
    c_list = [c for c in data_c if c in set(all_facts_df["concept"].unique())]
    if not c_list: return pd.DataFrame()
    df = all_facts_df[(all_facts_df["concept"].isin(c_list)) & (all_facts_df["fiscal_period"].isin({"Q1","Q2","Q3","Q4","FY"})) & (all_facts_df["period_type"] == "duration")].copy()
    if df.empty: return df
    df["period_start_dt"] = pd.to_datetime(df["period_start"], errors="coerce")
    df["period_end_dt"] = pd.to_datetime(df["period_end"], errors="coerce")
    df["calc_duration"] = (df["period_end_dt"] - df["period_start_dt"]).dt.days
    df = df.sort_values(["concept", "period_start", "fiscal_period", "calc_duration"], ascending=[True, True, True, False]).drop_duplicates(["concept", "period_start", "fiscal_period"], keep="first")
    derived = []
    for c in df["concept"].unique():
        cd = df[df["concept"] == c]
        for ps in cd["period_start"].unique():
            fg = cd[cd["period_start"] == ps]
            q1v = fg[fg["fiscal_period"]=="Q1"]["numeric_value"].iloc[0] if len(fg[fg["fiscal_period"]=="Q1"])>0 else None
            q2v = fg[fg["fiscal_period"]=="Q2"]["numeric_value"].iloc[0] if len(fg[fg["fiscal_period"]=="Q2"])>0 else None
            q3v = fg[fg["fiscal_period"]=="Q3"]["numeric_value"].iloc[0] if len(fg[fg["fiscal_period"]=="Q3"])>0 else None
            fyv = fg[fg["fiscal_period"]=="FY"]["numeric_value"].iloc[0] if len(fg[fg["fiscal_period"]=="FY"])>0 else None
            f_yr, unit = fg["fiscal_year"].iloc[0], fg["unit"].iloc[0] if "unit" in fg.columns else "USD"
            def _pe(fp): return fg[fg["fiscal_period"]==fp]["period_end"].iloc[0] if len(fg[fg["fiscal_period"]==fp])>0 else None
            if q1v is not None and pd.notna(q1v): derived.append({"concept":c,"period_end":_pe("Q1"),"fiscal_period":"Q1","fiscal_year":f_yr,"quarter_label":_period_end_to_quarter_label(_pe("Q1"),"Q1"),"numeric_value":q1v,"unit":unit,"period_type":"duration","statement_type":"cashflow"})
            if q2v is not None and pd.notna(q2v) and q1v is not None and pd.notna(q1v): derived.append({"concept":c,"period_end":_pe("Q2"),"fiscal_period":"Q2","fiscal_year":f_yr,"quarter_label":_period_end_to_quarter_label(_pe("Q2"),"Q2"),"numeric_value":q2v-q1v,"unit":unit,"period_type":"duration","statement_type":"cashflow"})
            if q3v is not None and pd.notna(q3v) and q2v is not None and pd.notna(q2v): derived.append({"concept":c,"period_end":_pe("Q3"),"fiscal_period":"Q3","fiscal_year":f_yr,"quarter_label":_period_end_to_quarter_label(_pe("Q3"),"Q3"),"numeric_value":q3v-q2v,"unit":unit,"period_type":"duration","statement_type":"cashflow"})
            if fyv is not None and pd.notna(fyv) and q3v is not None and pd.notna(q3v): derived.append({"concept":c,"period_end":_pe("FY"),"fiscal_period":"Q4","fiscal_year":f_yr,"quarter_label":_period_end_to_quarter_label(_pe("FY"),"Q4"),"numeric_value":fyv-q3v,"unit":unit,"period_type":"duration","statement_type":"cashflow"})
    if not derived: return pd.DataFrame()
    res = pd.DataFrame(derived).dropna(subset=["numeric_value"]).sort_values(["concept","quarter_label","fiscal_year"], ascending=[True,True,False]).drop_duplicates(["concept","quarter_label"], keep="first")
    res = _enrich_with_blueprint(res, bp)
    return _limit_quarters(res, num_quarters)


def upsert_quarterly_records(records: List[Dict[str, Any]]) -> int:
    """Insert or update quarterly fact records in the operational database."""
    if not records: return 0
    conn = _get_conn()
    now_iso = datetime.now(timezone.utc).isoformat()

    columns = ["ticker", "statement_type", "concept", "label", "quarter", "period_end",
               "fiscal_period", "fiscal_year", "numeric_value", "unit", "period_type",
               "depth", "is_total", "concept_order", "content_hash", "extracted_at", "created_at", "updated_at"]

    sql = f"""INSERT OR REPLACE INTO sf_quarterly_facts
              ({', '.join(columns)}) VALUES ({', '.join(['?']*len(columns))})"""

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
            elif hasattr(val, 'isoformat'):  # pandas Timestamp or datetime
                r[key] = str(val)
            elif isinstance(val, (int,)) and not isinstance(val, bool):
                r[key] = int(val)
            elif isinstance(val, float):
                r[key] = float(val)
            elif not isinstance(val, (str, bytes)):
                r[key] = str(val)

        defaults = {"fiscal_year": 0, "depth": 0, "is_total": 0, "concept_order": 0}
        values = tuple(r.get(c, defaults.get(c, "")) for c in columns)
        conn.execute(sql, values)

    conn.commit()
    return len(records)


def extract_and_persist(ticker: str, num_quarters: int, refresh: bool) -> Dict[str, Any]:
    """Main extraction entry point. Fetches from EDGAR and persists to DB."""
    from edgar import set_identity, Company
    import os

    ticker = ticker.upper()
    identity = os.environ.get("EDGAR_IDENTITY", "analyst@research.com")
    set_identity(identity)

    conn = _get_conn()

    if refresh:
        conn.execute("DELETE FROM sf_quarterly_facts WHERE ticker = ?", (ticker,))
        conn.commit()

    company = Company(ticker)
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT OR IGNORE INTO sf_tickers (ticker, company_name, cik) VALUES (?, ?, ?)",
                 (ticker, company.name, str(company.cik)))
    conn.commit()

    facts = company.get_facts()
    all_facts_df = facts.to_dataframe()
    blueprints = {s: get_statement_blueprint(facts, s) for s in ["income", "balance", "cashflow"]}

    processors = {"income": process_income, "balance": process_balance, "cashflow": process_cashflow}
    results = {}

    for stype, func in processors.items():
        try:
            df = func(all_facts_df, blueprints[stype], num_quarters)
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
