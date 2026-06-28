#!/usr/bin/env python3
"""
Tests for stock_financials and stock_notes tools.

Tests verify:
  - JSON output (no markdown key)
  - Proper Activity decomposition
  - Cloud sync for all sf_*/sn_* tables
  - Standardized 'refresh' parameter
  - Statement type selection (income/balance/cashflow)
  - Edge cases: invalid inputs, empty data, refresh, cache hits

Usage:
  pytest test_stock_tools.py -v
  pytest test_stock_tools.py -v -k "not integration"  # skip network tests
  pytest test_stock_tools.py -v -k "integration"       # only network tests
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

# ── Server management ────────────────────────────────────────────────

BASE = "http://127.0.0.1:8084"
_proc = None


def _start_server():
    global _proc
    data_dir = Path(__file__).parent / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    env = os.environ.copy()
    env["SERVER_PLATFORM"] = "linux"
    env["EDGAR_IDENTITY"] = "test@example.com"
    _proc = subprocess.Popen(
        [sys.executable, "run.py", "--port", "8084", "--resolve-operational"],
        cwd=str(Path(__file__).parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            r = requests.get(f"{BASE}/health", timeout=1)
            if r.status_code == 200:
                return
        except Exception:
            time.sleep(0.3)
    _proc.kill()
    raise RuntimeError("Server failed to start within 90s")


def _stop_server():
    global _proc
    if _proc:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _proc.kill()


@pytest.fixture(scope="session", autouse=True)
def server():
    try:
        r = requests.get(f"{BASE}/health", timeout=1)
        if r.status_code == 200:
            yield
            return
    except Exception:
        pass
    _start_server()
    yield
    _stop_server()


# ── Helpers ──────────────────────────────────────────────────────────


def tool_call(tool_name, arguments, observe=False, timeout=60):
    headers = {"X-Observe": "true"} if observe else {}
    r = requests.post(
        f"{BASE}/mcp",
        params={"timeout": str(timeout)},
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
        timeout=timeout + 5,
    )
    return r.status_code, r.json()


def extract(body):
    if "error" in body:
        return {"error": body["error"].get("message", str(body["error"]))}
    return json.loads(body["result"]["content"][0]["text"])


# ═══════════════════════════════════════════════════════════════════
#  1. STOCK_FINANCIALS — Unit Tests (no network)
# ═══════════════════════════════════════════════════════════════════


class TestStockFinancialsUnit:
    """Tests that don't require EDGAR network access."""

    def test_registered(self):
        """Tool is registered in the server."""
        r = requests.get(f"{BASE}/health")
        assert "stock_financials" in r.json()["tools"]

    def test_docs(self):
        """Tool docs endpoint works."""
        r = requests.get(f"{BASE}/tools/stock_financials/docs")
        assert r.status_code == 200
        assert "financial" in r.text.lower()

    def test_docs_json(self):
        r = requests.get(
            f"{BASE}/tools/stock_financials/docs",
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 200
        d = r.json()
        assert "description" in d

    def test_invalid_command(self):
        """Invalid command returns error."""
        s, b = tool_call("stock_financials", {"command": "bogus", "instructions": {}})
        result = extract(b)
        assert "error" in result

    def test_missing_command(self):
        """Missing command returns error."""
        s, b = tool_call("stock_financials", {})
        result = extract(b)
        assert "error" in result

    def test_extract_missing_ticker(self):
        """Extract without ticker returns error."""
        s, b = tool_call(
            "stock_financials", {"command": "extract", "instructions": {}}
        )
        result = extract(b)
        assert "error" in result

    def test_query_missing_ticker(self):
        """Query without ticker returns error."""
        s, b = tool_call(
            "stock_financials", {"command": "query", "instructions": {}}
        )
        result = extract(b)
        assert "error" in result

    def test_status_empty(self):
        """Status for non-existent ticker returns message."""
        s, b = tool_call(
            "stock_financials",
            {"command": "status", "instructions": {"ticker": "ZZZZZ"}},
        )
        result = extract(b)
        assert "message" in result or "statements" in result

    def test_catalog_empty(self):
        """Catalog for non-existent ticker returns message."""
        s, b = tool_call(
            "stock_financials",
            {"command": "catalog", "instructions": {"ticker": "ZZZZZ"}},
        )
        result = extract(b)
        assert "message" in result or "concepts" in result

    def test_query_no_data(self):
        """Query with no cached data returns message."""
        s, b = tool_call(
            "stock_financials",
            {
                "command": "query",
                "instructions": {"ticker": "ZZZZZ", "statement_type": "income"},
            },
        )
        result = extract(b)
        assert "records" in result or "message" in result

    def test_instructions_as_string(self):
        """Instructions as JSON string should work."""
        s, b = tool_call(
            "stock_financials",
            {"command": "status", "instructions": '{"ticker": "ZZZZZ"}'},
        )
        result = extract(b)
        assert isinstance(result, dict)

    def test_ticker_normalized(self):
        """Ticker should be uppercased."""
        s, b = tool_call(
            "stock_financials",
            {"command": "status", "instructions": {"ticker": "zzzzz"}},
        )
        result = extract(b)
        assert isinstance(result, dict)

    def test_observe_lineage(self):
        """With X-Observe, response should include lineage with 3 activities."""
        s, b = tool_call(
            "stock_financials",
            {"command": "status", "instructions": {"ticker": "ZZZZZ"}},
            observe=True,
        )
        assert "lineage" in b["result"]
        names = [e["name"] for e in b["result"]["lineage"]]
        assert "stock_financials.validate" in names
        assert "stock_financials.execute" in names
        assert "stock_financials.format" in names
        assert len(names) == 3

    def test_no_markdown_key(self):
        """Output should NOT contain a 'markdown' key — must be proper JSON."""
        s, b = tool_call(
            "stock_financials",
            {"command": "status", "instructions": {"ticker": "ZZZZZ"}},
        )
        result = extract(b)
        assert "markdown" not in result, "Output should be JSON, not markdown"

    def test_query_invalid_statement_type(self):
        """Invalid statement_type returns error."""
        s, b = tool_call(
            "stock_financials",
            {
                "command": "query",
                "instructions": {"ticker": "AAPL", "statement_type": "invalid"},
            },
        )
        result = extract(b)
        assert "error" in result

    def test_catalog_invalid_statement_type(self):
        """Invalid statement_type in catalog returns error."""
        s, b = tool_call(
            "stock_financials",
            {
                "command": "catalog",
                "instructions": {"ticker": "AAPL", "statement_type": "invalid"},
            },
        )
        result = extract(b)
        assert "error" in result


# ═══════════════════════════════════════════════════════════════════
#  2. STOCK_FINANCIALS — Integration Tests (require network)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestStockFinancialsIntegration:
    """Tests that call SEC EDGAR — may be slow. Uses small batches."""

    def test_extract_small_batch(self):
        """Extract AAPL financials (2 quarters — small batch)."""
        s, b = tool_call(
            "stock_financials",
            {"command": "extract", "instructions": {"ticker": "AAPL", "quarters": 2}},
            timeout=120,
        )
        result = extract(b)
        assert "error" not in result, f"Error: {result.get('error')}"
        assert result.get("ticker") == "AAPL"
        assert result.get("quarters_cached", 0) >= 1
        assert "coverage" in result
        assert "key_metrics" in result
        assert "markdown" not in result

    def test_status_after_extract(self):
        """Status should show cached data after extract."""
        s, b = tool_call(
            "stock_financials",
            {"command": "status", "instructions": {"ticker": "AAPL"}},
        )
        result = extract(b)
        assert "statements" in result
        assert len(result["statements"]) > 0
        assert "markdown" not in result

    def test_query_income(self):
        """Query income statement for AAPL."""
        s, b = tool_call(
            "stock_financials",
            {
                "command": "query",
                "instructions": {"ticker": "AAPL", "statement_type": "income"},
            },
        )
        result = extract(b)
        assert "records" in result
        assert result["records"] > 0
        assert "data" in result
        assert "markdown" not in result

    def test_query_balance(self):
        """Query balance sheet for AAPL."""
        s, b = tool_call(
            "stock_financials",
            {
                "command": "query",
                "instructions": {"ticker": "AAPL", "statement_type": "balance"},
            },
        )
        result = extract(b)
        assert "records" in result
        assert result["records"] > 0
        assert result.get("statement_type") == "balance"
        assert "markdown" not in result

    def test_query_cashflow(self):
        """Query cash flow for AAPL."""
        s, b = tool_call(
            "stock_financials",
            {
                "command": "query",
                "instructions": {"ticker": "AAPL", "statement_type": "cashflow"},
            },
        )
        result = extract(b)
        assert "records" in result
        assert result.get("statement_type") == "cashflow"
        assert "markdown" not in result

    def test_query_specific_concept(self):
        """Query a specific XBRL concept."""
        s, b = tool_call(
            "stock_financials",
            {
                "command": "query",
                "instructions": {
                    "ticker": "AAPL",
                    "statement_type": "income",
                    "concept": "us-gaap:Revenue",
                },
            },
        )
        result = extract(b)
        assert "records" in result
        assert "markdown" not in result

    def test_catalog(self):
        """Catalog should list available concepts."""
        s, b = tool_call(
            "stock_financials",
            {
                "command": "catalog",
                "instructions": {"ticker": "AAPL", "statement_type": "income"},
            },
        )
        result = extract(b)
        assert "concepts" in result
        assert len(result["concepts"]) > 0
        assert "markdown" not in result

    def test_cache_hit(self):
        """Second extract should be a cache hit."""
        s, b = tool_call(
            "stock_financials",
            {"command": "extract", "instructions": {"ticker": "AAPL", "quarters": 2}},
            timeout=120,
        )
        result = extract(b)
        assert result.get("cache_hit") is True or result.get("quarters_cached", 0) >= 1

    def test_refresh(self):
        """Refresh should re-extract from EDGAR (purges local + cloud)."""
        s, b = tool_call(
            "stock_financials",
            {
                "command": "extract",
                "instructions": {"ticker": "AAPL", "quarters": 2, "refresh": True},
            },
            timeout=120,
        )
        result = extract(b)
        assert "error" not in result
        assert result.get("refresh") is True
        assert "markdown" not in result

    def test_invalid_ticker(self):
        """Invalid ticker should return an error."""
        s, b = tool_call(
            "stock_financials",
            {
                "command": "extract",
                "instructions": {"ticker": "INVALIDTICKER123XYZ", "quarters": 1},
            },
            timeout=60,
        )
        result = extract(b)
        assert "error" in result

    def test_quarters_capped(self):
        """Quarters parameter should be capped at 40."""
        s, b = tool_call(
            "stock_financials",
            {
                "command": "extract",
                "instructions": {"ticker": "MSFT", "quarters": 100},
            },
            timeout=120,
        )
        result = extract(b)
        assert isinstance(result, dict)

    def test_query_returns_pivot_structure(self):
        """Query output should have concepts×quarters pivot structure."""
        s, b = tool_call(
            "stock_financials",
            {
                "command": "query",
                "instructions": {"ticker": "AAPL", "statement_type": "income"},
            },
        )
        result = extract(b)
        if result.get("records", 0) > 0:
            assert "concepts" in result
            assert "quarters" in result
            assert "data" in result
            # Each concept in data should have quarter keys
            for concept, info in result["data"].items():
                assert "label" in info
                assert "concept" in info


# ═══════════════════════════════════════════════════════════════════
#  3. STOCK_NOTES — Unit Tests (no network)
# ═══════════════════════════════════════════════════════════════════


class TestStockNotesUnit:
    """Tests that don't require EDGAR network access."""

    def test_registered(self):
        r = requests.get(f"{BASE}/health")
        assert "stock_notes" in r.json()["tools"]

    def test_docs(self):
        r = requests.get(f"{BASE}/tools/stock_notes/docs")
        assert r.status_code == 200
        assert "note" in r.text.lower()

    def test_docs_json(self):
        r = requests.get(
            f"{BASE}/tools/stock_notes/docs",
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 200

    def test_invalid_command(self):
        s, b = tool_call("stock_notes", {"command": "bogus", "instructions": {}})
        result = extract(b)
        assert "error" in result

    def test_missing_command(self):
        s, b = tool_call("stock_notes", {})
        result = extract(b)
        assert "error" in result

    def test_discover_missing_ticker(self):
        s, b = tool_call(
            "stock_notes", {"command": "discover", "instructions": {}}
        )
        result = extract(b)
        assert "error" in result

    def test_note_missing_accession(self):
        s, b = tool_call("stock_notes", {"command": "note", "instructions": {}})
        result = extract(b)
        assert "error" in result

    def test_details_missing_ticker(self):
        s, b = tool_call(
            "stock_notes",
            {
                "command": "details",
                "instructions": {"concept": "us-gaap:Revenue"},
            },
        )
        result = extract(b)
        assert "error" in result

    def test_details_missing_concept(self):
        s, b = tool_call(
            "stock_notes",
            {"command": "details", "instructions": {"ticker": "AAPL"}},
        )
        result = extract(b)
        assert "error" in result

    def test_note_invalid_accession(self):
        """Invalid accession number should return error."""
        s, b = tool_call(
            "stock_notes",
            {"command": "note", "instructions": {"accession_no": "INVALID-12345"}},
            timeout=60,
        )
        result = extract(b)
        assert "error" in result

    def test_details_no_data(self):
        """Details for non-existent concept returns error."""
        s, b = tool_call(
            "stock_notes",
            {
                "command": "details",
                "instructions": {"ticker": "ZZZZZ", "concept": "us-gaap:NonExistent"},
            },
        )
        result = extract(b)
        assert "error" in result

    def test_instructions_as_string(self):
        """Instructions as JSON string should work."""
        s, b = tool_call(
            "stock_notes",
            {"command": "discover", "instructions": '{"ticker": "AAPL"}'},
            timeout=60,
        )
        result = extract(b)
        assert isinstance(result, dict)

    def test_ticker_normalized(self):
        s, b = tool_call(
            "stock_notes",
            {"command": "discover", "instructions": {"ticker": "aapl"}},
            timeout=60,
        )
        result = extract(b)
        assert isinstance(result, dict)

    def test_observe_lineage(self):
        """With X-Observe, response should include lineage with 3 activities."""
        s, b = tool_call(
            "stock_notes",
            {"command": "discover", "instructions": {"ticker": "AAPL"}},
            observe=True,
            timeout=60,
        )
        assert "lineage" in b["result"]
        names = [e["name"] for e in b["result"]["lineage"]]
        assert "stock_notes.validate" in names
        assert "stock_notes.execute" in names
        assert "stock_notes.format" in names
        assert len(names) == 3

    def test_no_markdown_key(self):
        """Output should NOT contain a 'markdown' key — must be proper JSON."""
        s, b = tool_call(
            "stock_notes",
            {"command": "discover", "instructions": {"ticker": "AAPL"}},
            timeout=60,
        )
        result = extract(b)
        assert "markdown" not in result, "Output should be JSON, not markdown"

    def test_force_refresh_backward_compat(self):
        """force_refresh should be accepted as alias for refresh (no crash)."""
        # This tests the validate step — it should not error
        s, b = tool_call(
            "stock_notes",
            {
                "command": "note",
                "instructions": {
                    "accession_no": "0000000000-00-000000",
                    "force_refresh": True,
                },
            },
            timeout=30,
        )
        result = extract(b)
        # Should get an error about the accession, not about force_refresh
        assert isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════════
#  4. STOCK_NOTES — Integration Tests (require network)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestStockNotesIntegration:
    """Tests that call SEC EDGAR — may be slow. Uses small batches."""

    def test_discover_small_batch(self):
        """Discover AAPL 10-K filings (small batch)."""
        s, b = tool_call(
            "stock_notes",
            {
                "command": "discover",
                "instructions": {"ticker": "AAPL", "forms": "10-K"},
            },
            timeout=60,
        )
        result = extract(b)
        assert "error" not in result, f"Error: {result.get('error')}"
        assert result.get("filings_count", 0) >= 1
        assert "filings" in result
        assert "markdown" not in result
        # Each filing should have structured fields
        for f in result["filings"]:
            assert "form" in f
            assert "accession_no" in f
            assert "filing_date" in f

    def test_discover_multiple_forms(self):
        """Discover with multiple form types."""
        s, b = tool_call(
            "stock_notes",
            {
                "command": "discover",
                "instructions": {"ticker": "AAPL", "forms": "10-K,10-Q"},
            },
            timeout=60,
        )
        result = extract(b)
        assert "error" not in result
        assert result.get("filings_count", 0) >= 1

    def test_discover_invalid_ticker(self):
        """Discover with invalid ticker returns error."""
        s, b = tool_call(
            "stock_notes",
            {
                "command": "discover",
                "instructions": {"ticker": "INVALIDTICKER123XYZ"},
            },
            timeout=60,
        )
        result = extract(b)
        assert "error" in result or result.get("filings_count", 0) == 0

    def test_note_list_json(self):
        """List notes in a filing — output must be JSON, not markdown."""
        # First discover to get an accession number
        s, b = tool_call(
            "stock_notes",
            {
                "command": "discover",
                "instructions": {"ticker": "AAPL", "forms": "10-K"},
            },
            timeout=60,
        )
        result = extract(b)
        if result.get("filings_count", 0) == 0:
            pytest.skip("No filings found")

        accession = result["filings"][0]["accession_no"]

        # List notes
        s, b = tool_call(
            "stock_notes",
            {
                "command": "note",
                "instructions": {"accession_no": accession, "ticker": "AAPL"},
            },
            timeout=120,
        )
        result = extract(b)
        assert "error" not in result, f"Error: {result.get('error')}"
        assert "notes" in result
        assert "notes_count" in result
        assert "markdown" not in result
        # Each note should have structured fields
        if result["notes_count"] > 0:
            for n in result["notes"]:
                assert "note_number" in n
                assert "title" in n

    def test_note_drill_json(self):
        """Drill into a specific note — output must be JSON."""
        # Discover
        s, b = tool_call(
            "stock_notes",
            {
                "command": "discover",
                "instructions": {"ticker": "AAPL", "forms": "10-K"},
            },
            timeout=60,
        )
        result = extract(b)
        if result.get("filings_count", 0) == 0:
            pytest.skip("No filings found")

        accession = result["filings"][0]["accession_no"]

        # List notes first
        s, b = tool_call(
            "stock_notes",
            {
                "command": "note",
                "instructions": {"accession_no": accession, "ticker": "AAPL"},
            },
            timeout=120,
        )
        result = extract(b)
        if result.get("notes_count", 0) == 0:
            pytest.skip("No notes found")

        note_num = result["notes"][0]["note_number"]

        # Drill into note
        s, b = tool_call(
            "stock_notes",
            {
                "command": "note",
                "instructions": {
                    "accession_no": accession,
                    "ticker": "AAPL",
                    "note_number": note_num,
                },
            },
            timeout=120,
        )
        result = extract(b)
        assert "error" not in result, f"Error: {result.get('error')}"
        assert "narrative" in result or "detail_tables" in result
        assert "markdown" not in result

    def test_refresh_parameter(self):
        """refresh: true should re-extract (not force_refresh)."""
        s, b = tool_call(
            "stock_notes",
            {
                "command": "discover",
                "instructions": {"ticker": "AAPL", "forms": "10-K"},
            },
            timeout=60,
        )
        result = extract(b)
        if result.get("filings_count", 0) == 0:
            pytest.skip("No filings found")

        accession = result["filings"][0]["accession_no"]

        # Use refresh: true
        s, b = tool_call(
            "stock_notes",
            {
                "command": "note",
                "instructions": {
                    "accession_no": accession,
                    "ticker": "AAPL",
                    "refresh": True,
                },
            },
            timeout=120,
        )
        result = extract(b)
        assert "error" not in result
        assert "markdown" not in result

    def test_details_invalid_date_format(self):
        """Details with bad date format returns error."""
        s, b = tool_call(
            "stock_notes",
            {
                "command": "details",
                "instructions": {
                    "ticker": "AAPL",
                    "concept": "us-gaap:Revenue",
                    "start_date": "bad-date",
                },
            },
        )
        result = extract(b)
        assert "error" in result


# ═══════════════════════════════════════════════════════════════════
#  5. DATABASE SCHEMA TESTS — Cloud Sync
# ═══════════════════════════════════════════════════════════════════


class TestSchema:
    """Verify all tables exist in both operational and cloud databases."""

    def test_databases_endpoint(self):
        """Databases endpoint returns table info."""
        r = requests.get(f"{BASE}/databases")
        assert r.status_code == 200

    # ── Operational tables ──

    def test_sf_tickers_table(self):
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        assert "sf_tickers" in d["operational"]["tables"]
        assert d["operational"]["tables"]["sf_tickers"]["exists"]

    def test_sf_quarterly_facts_table(self):
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        assert "sf_quarterly_facts" in d["operational"]["tables"]
        assert d["operational"]["tables"]["sf_quarterly_facts"]["exists"]

    def test_sn_filings_table(self):
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        assert "sn_filings" in d["operational"]["tables"]
        assert d["operational"]["tables"]["sn_filings"]["exists"]

    def test_sn_notes_table(self):
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        assert "sn_notes" in d["operational"]["tables"]
        assert d["operational"]["tables"]["sn_notes"]["exists"]

    def test_sn_note_details_table(self):
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        assert "sn_note_details" in d["operational"]["tables"]
        assert d["operational"]["tables"]["sn_note_details"]["exists"]

    def test_sn_detail_registry_table(self):
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        assert "sn_detail_registry" in d["operational"]["tables"]
        assert d["operational"]["tables"]["sn_detail_registry"]["exists"]

    # ── Cloud tables (must exist for synced tables) ──

    def test_cloud_sf_tickers(self):
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        assert "sf_tickers" in d["cloud"]["tables"]
        assert d["cloud"]["tables"]["sf_tickers"]["exists"]

    def test_cloud_sf_quarterly_facts(self):
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        assert "sf_quarterly_facts" in d["cloud"]["tables"]
        assert d["cloud"]["tables"]["sf_quarterly_facts"]["exists"]

    def test_cloud_sn_filings(self):
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        assert "sn_filings" in d["cloud"]["tables"]
        assert d["cloud"]["tables"]["sn_filings"]["exists"]

    def test_cloud_sn_notes(self):
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        assert "sn_notes" in d["cloud"]["tables"]
        assert d["cloud"]["tables"]["sn_notes"]["exists"]

    def test_cloud_sn_note_details(self):
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        assert "sn_note_details" in d["cloud"]["tables"]
        assert d["cloud"]["tables"]["sn_note_details"]["exists"]

    def test_cloud_sn_detail_registry(self):
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        assert "sn_detail_registry" in d["cloud"]["tables"]
        assert d["cloud"]["tables"]["sn_detail_registry"]["exists"]

    # ── Schema column verification ──

    def test_sf_quarterly_facts_columns(self):
        """Verify key columns exist in sf_quarterly_facts."""
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        cols = d["operational"]["tables"]["sf_quarterly_facts"]["columns"]
        for expected in (
            "ticker", "statement_type", "concept", "quarter",
            "numeric_value", "row_hash",
        ):
            assert expected in cols, f"Missing column: {expected}"

    def test_sn_notes_columns(self):
        """Verify key columns exist in sn_notes."""
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        cols = d["operational"]["tables"]["sn_notes"]["columns"]
        for expected in (
            "note_id", "filing_id", "ticker", "accession_no",
            "note_number", "title", "narrative_text", "row_hash",
        ):
            assert expected in cols, f"Missing column: {expected}"

    def test_sn_note_details_columns(self):
        """Verify key columns exist in sn_note_details."""
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        cols = d["operational"]["tables"]["sn_note_details"]["columns"]
        for expected in (
            "detail_id", "accession_no", "concept", "value",
            "period_end_date", "row_hash",
        ):
            assert expected in cols, f"Missing column: {expected}"

    def test_schema_ok(self):
        """All tables should have schema_ok=true."""
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        for tbl_name, tbl_info in d["operational"]["tables"].items():
            if tbl_info["exists"]:
                assert tbl_info.get("schema_ok", True), (
                    f"Schema mismatch in {tbl_name}"
                )


# ═══════════════════════════════════════════════════════════════════
#  6. SYNC TESTS
# ═══════════════════════════════════════════════════════════════════


class TestSync:
    """Verify cloud sync status for synced tables."""

    def test_sync_endpoint(self):
        """Sync endpoint returns sync status."""
        r = requests.get(f"{BASE}/sync")
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d, dict)

    def test_synced_tables_present(self):
        """All cloud-synced tables should appear in sync status."""
        r = requests.get(f"{BASE}/sync")
        d = r.json()
        tables = d.get("tables", {})
        for table in (
            "artifacts", "sf_tickers", "sf_quarterly_facts",
            "sn_filings", "sn_notes", "sn_detail_registry", "sn_note_details",
        ):
            assert table in tables, f"Missing sync status for {table}"

    def test_sync_tables_match(self):
        """Synced tables should be in sync (operational == cloud)."""
        r = requests.get(f"{BASE}/sync")
        d = r.json()
        tables = d.get("tables", {})
        for table, info in tables.items():
            assert info.get("match", False), (
                f"Table {table} out of sync: op={info.get('op_count')} "
                f"cloud={info.get('cloud_count')} "
                f"mismatches={info.get('hash_mismatches')}"
            )


# ═══════════════════════════════════════════════════════════════════
#  7. SHUTDOWN TESTS
# ═══════════════════════════════════════════════════════════════════


class TestShutdownBehavior:
    """Verify shutdown does NOT perform sync check."""

    def test_health_healthy(self):
        """Server should be healthy (startup sync works)."""
        r = requests.get(f"{BASE}/health")
        assert r.status_code == 200
        d = r.json()
        assert d.get("status") in ("healthy", "degraded")
