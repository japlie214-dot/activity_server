# tools/stock_notes/extractor.py
"""Stock Notes Extractor — fetches filing footnotes from SEC EDGAR and persists to DB."""
import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

log = logging.getLogger("activity-server")
APPROVED_FORMS = {"10-K", "10-Q", "20-F", "6-K"}


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


def _set_edgar_identity():
    import os
    from edgar import set_identity
    identity = os.environ.get("EDGAR_IDENTITY", "analyst@research.com")
    set_identity(identity)


def discover_filings(ticker: str, form_types: Optional[List[str]] = None, limit: int = 40) -> List[Dict[str, Any]]:
    """Discover recent filings for a ticker."""
    from edgar import Company
    from .fiscal import get_fiscal_year_end_month, fiscal_quarter_from_period_end

    valid_forms = [f.strip().upper() for f in (form_types or APPROVED_FORMS) if f.strip().upper() in APPROVED_FORMS]
    if not valid_forms:
        return []

    _set_edgar_identity()
    company = Company(ticker)
    fy_month = get_fiscal_year_end_month(ticker, company=company)

    results = []
    for form in valid_forms:
        try:
            filings = company.get_filings(form=form, amendments=False)
            if not filings:
                continue
            for count, f in enumerate(filings):
                if count >= 20:
                    break
                period = str(getattr(f, "period_of_report", "") or getattr(f, "period_of_report_date", "") or "")
                quarter, year = 0, 0
                if period:
                    try:
                        quarter, year = fiscal_quarter_from_period_end(
                            datetime.strptime(period[:10], "%Y-%m-%d").date(), fy_month)
                    except ValueError:
                        pass
                results.append({
                    "ticker": ticker.upper(), "company_name": company.name, "cik": str(company.cik),
                    "form": f.form, "filing_date": str(f.filing_date), "accession_no": f.accession_no,
                    "period_of_report": period, "quarter": quarter, "year": year,
                    "fiscal_year_end_month": fy_month,
                })
        except Exception as e:
            log.warning(f"StockNotes:Discover:{form} — {e}")

    results.sort(key=lambda x: x.get("filing_date", ""), reverse=True)
    return results[:limit]


def extract_and_persist_filing(accession_no: str, ticker: str = "", form: str = "",
                               job_id: str = "", force_refresh: bool = False) -> Dict[str, Any]:
    """Extract filing footnotes from EDGAR and persist to DB."""
    from edgar import Company, find as edgar_find
    from .fiscal import get_fiscal_year_end_month, fiscal_quarter_from_period_end
    from .detail_manager import upsert_tidy_records, register_detail_table, delete_filing_data
    from .tidy_transform import transform_to_tidy

    _set_edgar_identity()
    conn = _get_conn()
    filing = edgar_find(search_id=accession_no)
    if not filing:
        raise ValueError(f"Filing {accession_no} not found in EDGAR.")

    dw = _get_dual_writer()

    if force_refresh:
        from .detail_manager import delete_filing_data
        deleted = delete_filing_data(accession_no)
        if deleted > 0:
            log.info(f"StockNotes:Rehydrate — deleted {deleted} rows for {accession_no}")

    cik = getattr(filing, 'cik', 0)
    if not ticker and cik:
        try:
            comp = Company(cik)
            ticker = comp.tickers[0].upper() if hasattr(comp, 'tickers') and comp.tickers else str(cik)
        except Exception:
            ticker = str(cik)
    elif not ticker:
        ticker = "UNKNOWN"

    if not form:
        form = filing.form

    company = Company(cik) if cik else Company(ticker)
    fy_month = get_fiscal_year_end_month(ticker, company=company)

    obj = filing.obj()
    period = str(getattr(obj, "period_of_report", ""))
    quarter, year = 0, 0
    if period:
        try:
            quarter, year = fiscal_quarter_from_period_end(
                datetime.strptime(period[:10], "%Y-%m-%d").date(), fy_month)
        except ValueError:
            pass

    filing_id = f"{ticker}|{form}|{accession_no}"
    filing_hash = hashlib.md5(f"{ticker}|{form}|{accession_no}|{period}|{cik}".encode()).hexdigest()
    now_iso = datetime.utcnow().isoformat()

    filing_data = {
        "filing_id": filing_id,
        "ticker": ticker,
        "form": form,
        "filing_date": str(filing.filing_date),
        "accession_no": accession_no,
        "period_of_report": period,
        "company_name": str(getattr(filing, 'company', 'Unknown')),
        "cik": str(cik),
        "fiscal_year_end_month": fy_month,
        "quarter": quarter,
        "year": year,
        "content_hash": filing_hash,
        "updated_at": now_iso,
    }
    if dw:
        dw.upsert("sn_filings", filing_data)
    else:
        conn.execute(
            """INSERT OR REPLACE INTO sn_filings
               (filing_id, ticker, form, filing_date, accession_no, period_of_report,
                company_name, cik, fiscal_year_end_month, quarter, year, content_hash, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (filing_id, ticker, form, str(filing.filing_date), accession_no, period,
             str(getattr(filing, 'company', 'Unknown')), str(cik), fy_month, quarter, year, filing_hash)
        )
        conn.commit()

    if not hasattr(obj, "notes") or not obj.notes:
        return {"filing_id": filing_id, "ticker": ticker, "accession_no": accession_no,
                "note_count": 0, "detail_table_count": 0}

    q_status = "direct" if form in ("10-Q", "6-K") else ("from_annual_filing" if form in ("10-K", "20-F") else "unknown")
    total_detail_tables = 0

    for note in list(obj.notes):
        try:
            note_id = f"{filing_id}|N{note.number}"
            narrative = getattr(note, "text", "") or ""
            narrative_hash = hashlib.md5(narrative.encode()).hexdigest() if narrative else ""

            expands = getattr(note, "expands", None) or []
            expands_statements = getattr(note, "expands_statements", None) or []

            table_count = len(note.tables) if hasattr(note, "tables") and note.tables else 0
            details_count = len(note.details) if hasattr(note, "details") and note.details else 0
            note_hash = hashlib.md5(f"{note.number}|{note.title}|{narrative_hash}|{table_count}|{details_count}".encode()).hexdigest()

            # Process detail tables
            if hasattr(note, "details") and note.details:
                for di, d in enumerate(note.details):
                    try:
                        df = d.to_dataframe()
                        if df is not None and not df.empty:
                            detail_title = str(d)[:100].split("\n")[0] if str(d) else f"Detail {di}"
                            safe_title = (detail_title or "").strip()
                            dt_name = re.sub(r'[^a-zA-Z0-9]', '_', safe_title or f"Note{note.number}_D{di}")[:40].strip('_') + f"_D{di}"
                            try:
                                tidy_records, unique_concepts = transform_to_tidy(
                                    df, ticker, form, accession_no, note.number, di)
                                count = upsert_tidy_records(tidy_records)
                                register_detail_table(ticker, dt_name, detail_title, note.number,
                                                      accession_no, "detail", unique_concepts, count,
                                                      quarter, year, q_status)
                                total_detail_tables += 1
                            except Exception as e:
                                log.warning(f"StockNotes:MalformedTable:{accession_no}:N{note.number} — {e}")
                    except Exception:
                        pass

            # Insert note record
            note_data = {
                "note_id": note_id,
                "filing_id": filing_id,
                "ticker": ticker,
                "form": form,
                "accession_no": accession_no,
                "note_number": note.number,
                "title": note.title,
                "short_name": getattr(note, "short_name", note.title) or note.title,
                "narrative_text": narrative,
                "narrative_hash": narrative_hash,
                "expands": json.dumps(expands),
                "expands_statements": json.dumps(expands_statements),
                "table_count": table_count,
                "details_count": details_count,
                "quarter": quarter,
                "year": year,
                "quarterly_status": q_status,
                "version": 1,
                "content_hash": note_hash,
                "updated_at": now_iso,
            }
            if dw:
                dw.upsert("sn_notes", note_data)
            else:
                conn.execute(
                    """INSERT OR REPLACE INTO sn_notes
                       (note_id, filing_id, ticker, form, accession_no, note_number, title, short_name,
                        narrative_text, narrative_hash, expands, expands_statements,
                        table_count, details_count, quarter, year, quarterly_status, version, content_hash, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                    (note_id, filing_id, ticker, form, accession_no, note.number, note.title,
                     getattr(note, "short_name", note.title) or note.title,
                     narrative, narrative_hash, json.dumps(expands), json.dumps(expands_statements),
                     table_count, details_count, quarter, year, q_status, 1, note_hash)
                )
                conn.commit()
        except Exception as e:
            log.warning(f"StockNotes:Note:{accession_no}:N{note.number} — {e}")

    return {"filing_id": filing_id, "ticker": ticker, "accession_no": accession_no,
            "note_count": len(obj.notes), "detail_table_count": total_detail_tables}
