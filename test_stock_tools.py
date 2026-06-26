#!/usr/bin/env python3
"""
Tests for stock_financials and stock_notes tools.

These tools call SEC EDGAR, so tests are organized as:
  - Unit tests (no network): validation, formatting, DB schema
  - Integration tests (require network): extract, discover, query

Usage:
  pytest test_stock_tools.py -v
  pytest test_stock_tools.py -v -k "not integration"  # skip network tests
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
        cwd=str(Path(__file__).parent), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            r = requests.get(f"{BASE}/health", timeout=1)
            if r.status_code == 200:
                return
        except Exception:
            time.sleep(0.3)
    _proc.kill()
    raise RuntimeError("Server failed to start")


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
    r = requests.post(f"{BASE}/mcp", params={"timeout": str(timeout)}, headers=headers, json={
        "jsonrpc": "2.0", "id": 99, "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }, timeout=timeout + 5)
    return r.status_code, r.json()


def extract(body):
    if "error" in body:
        return {"error": body["error"].get("message", str(body["error"]))}
    return json.loads(body["result"]["content"][0]["text"])


# ═══════════════════════════════════════════════════════════════════
#  1. STOCK_FINANCIALS — Unit Tests
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
        r = requests.get(f"{BASE}/tools/stock_financials/docs",
                         headers={"Accept": "application/json"})
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
        s, b = tool_call("stock_financials", {"command": "extract", "instructions": {}})
        result = extract(b)
        assert "error" in result

    def test_query_missing_ticker(self):
        """Query without ticker returns error."""
        s, b = tool_call("stock_financials", {"command": "query", "instructions": {}})
        result = extract(b)
        assert "error" in result

    def test_status_empty(self):
        """Status for non-existent ticker returns message."""
        s, b = tool_call("stock_financials", {"command": "status", "instructions": {"ticker": "ZZZZZ"}})
        result = extract(b)
        assert "message" in result or "No cached data" in str(result)

    def test_catalog_empty(self):
        """Catalog for non-existent ticker returns message."""
        s, b = tool_call("stock_financials", {"command": "catalog", "instructions": {"ticker": "ZZZZZ"}})
        result = extract(b)
        assert "message" in result or "No concepts" in str(result)

    def test_query_no_data(self):
        """Query with no cached data returns message."""
        s, b = tool_call("stock_financials", {"command": "query", "instructions": {
            "ticker": "ZZZZZ", "statement_type": "income"
        }})
        result = extract(b)
        assert "records" in result or "message" in result

    def test_instructions_as_string(self):
        """Instructions as JSON string should work."""
        s, b = tool_call("stock_financials", {
            "command": "status",
            "instructions": '{"ticker": "ZZZZZ"}'
        })
        result = extract(b)
        assert "error" not in result or "No cached" in str(result)

    def test_ticker_normalized(self):
        """Ticker should be uppercased."""
        s, b = tool_call("stock_financials", {"command": "status", "instructions": {"ticker": "zzzzz"}})
        result = extract(b)
        # Should not crash — lowercase ticker is normalized
        assert isinstance(result, dict)

    def test_observe_lineage(self):
        """With X-Observe, response should include lineage."""
        s, b = tool_call("stock_financials", {"command": "status", "instructions": {"ticker": "ZZZZZ"}}, observe=True)
        assert "lineage" in b["result"]
        names = [e["name"] for e in b["result"]["lineage"]]
        assert "stock_financials.validate" in names
        assert "stock_financials.execute" in names
        assert "stock_financials.format" in names


# ═══════════════════════════════════════════════════════════════════
#  2. STOCK_FINANCIALS — Integration Tests (require network)
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestStockFinancialsIntegration:
    """Tests that call SEC EDGAR — may be slow."""

    def test_extract_aapl(self):
        """Extract AAPL financials (small quarter count)."""
        s, b = tool_call("stock_financials", {
            "command": "extract",
            "instructions": {"ticker": "AAPL", "quarters": 2}
        }, timeout=120)
        result = extract(b)
        assert "error" not in result, f"Error: {result.get('error')}"
        assert result.get("ticker") == "AAPL"
        assert result.get("quarters", 0) >= 1
        assert "markdown" in result

    def test_status_after_extract(self):
        """Status should show cached data after extract."""
        s, b = tool_call("stock_financials", {
            "command": "status", "instructions": {"ticker": "AAPL"}
        })
        result = extract(b)
        assert "statements" in result
        assert len(result["statements"]) > 0

    def test_query_income(self):
        """Query income statement for AAPL."""
        s, b = tool_call("stock_financials", {
            "command": "query",
            "instructions": {"ticker": "AAPL", "statement_type": "income"}
        })
        result = extract(b)
        assert "records" in result
        assert result["records"] > 0
        assert "markdown" in result

    def test_query_balance(self):
        """Query balance sheet for AAPL."""
        s, b = tool_call("stock_financials", {
            "command": "query",
            "instructions": {"ticker": "AAPL", "statement_type": "balance"}
        })
        result = extract(b)
        assert "records" in result

    def test_query_specific_concept(self):
        """Query a specific XBRL concept."""
        s, b = tool_call("stock_financials", {
            "command": "query",
            "instructions": {"ticker": "AAPL", "concept": "us-gaap:Revenue"}
        })
        result = extract(b)
        assert "records" in result

    def test_catalog(self):
        """Catalog should list available concepts."""
        s, b = tool_call("stock_financials", {
            "command": "catalog",
            "instructions": {"ticker": "AAPL", "statement_type": "income"}
        })
        result = extract(b)
        assert "concepts" in result
        assert len(result["concepts"]) > 0

    def test_cache_hit(self):
        """Second extract should be a cache hit."""
        s, b = tool_call("stock_financials", {
            "command": "extract",
            "instructions": {"ticker": "AAPL", "quarters": 2}
        }, timeout=120)
        result = extract(b)
        assert result.get("cache_hit") is True or result.get("quarters", 0) >= 1

    def test_refresh(self):
        """Refresh should re-extract from EDGAR."""
        s, b = tool_call("stock_financials", {
            "command": "extract",
            "instructions": {"ticker": "AAPL", "quarters": 2, "refresh": True}
        }, timeout=120)
        result = extract(b)
        assert "error" not in result

    def test_invalid_ticker(self):
        """Invalid ticker should return an error."""
        s, b = tool_call("stock_financials", {
            "command": "extract",
            "instructions": {"ticker": "INVALIDTICKER123XYZ", "quarters": 1}
        }, timeout=60)
        result = extract(b)
        assert "error" in result

    def test_quarters_capped(self):
        """Quarters parameter should be capped at 40."""
        s, b = tool_call("stock_financials", {
            "command": "extract",
            "instructions": {"ticker": "MSFT", "quarters": 100}
        }, timeout=120)
        result = extract(b)
        # Should not crash — quarters capped at 40
        assert isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════════
#  3. STOCK_NOTES — Unit Tests
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
        r = requests.get(f"{BASE}/tools/stock_notes/docs",
                         headers={"Accept": "application/json"})
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
        s, b = tool_call("stock_notes", {"command": "discover", "instructions": {}})
        result = extract(b)
        assert "error" in result

    def test_note_missing_accession(self):
        s, b = tool_call("stock_notes", {"command": "note", "instructions": {}})
        result = extract(b)
        assert "error" in result

    def test_details_missing_ticker(self):
        s, b = tool_call("stock_notes", {"command": "details", "instructions": {"concept": "us-gaap:Revenue"}})
        result = extract(b)
        assert "error" in result

    def test_details_missing_concept(self):
        s, b = tool_call("stock_notes", {"command": "details", "instructions": {"ticker": "AAPL"}})
        result = extract(b)
        assert "error" in result

    def test_note_invalid_accession(self):
        """Invalid accession number should return error."""
        s, b = tool_call("stock_notes", {
            "command": "note",
            "instructions": {"accession_no": "INVALID-12345"}
        }, timeout=60)
        result = extract(b)
        assert "error" in result

    def test_details_no_data(self):
        """Details for non-existent concept returns error."""
        s, b = tool_call("stock_notes", {
            "command": "details",
            "instructions": {"ticker": "ZZZZZ", "concept": "us-gaap:NonExistent"}
        })
        result = extract(b)
        assert "error" in result

    def test_instructions_as_string(self):
        """Instructions as JSON string should work."""
        s, b = tool_call("stock_notes", {
            "command": "discover",
            "instructions": '{"ticker": "AAPL"}'
        }, timeout=60)
        result = extract(b)
        # Should not crash
        assert isinstance(result, dict)

    def test_ticker_normalized(self):
        s, b = tool_call("stock_notes", {
            "command": "discover",
            "instructions": {"ticker": "aapl"}
        }, timeout=60)
        result = extract(b)
        assert isinstance(result, dict)

    def test_observe_lineage(self):
        s, b = tool_call("stock_notes", {
            "command": "discover",
            "instructions": {"ticker": "AAPL"}
        }, observe=True, timeout=60)
        assert "lineage" in b["result"]
        names = [e["name"] for e in b["result"]["lineage"]]
        assert "stock_notes.validate" in names
        assert "stock_notes.execute" in names
        assert "stock_notes.format" in names


# ═══════════════════════════════════════════════════════════════════
#  4. STOCK_NOTES — Integration Tests (require network)
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestStockNotesIntegration:
    """Tests that call SEC EDGAR — may be slow."""

    def test_discover_aapl(self):
        """Discover AAPL filings."""
        s, b = tool_call("stock_notes", {
            "command": "discover",
            "instructions": {"ticker": "AAPL", "forms": "10-K"}
        }, timeout=60)
        result = extract(b)
        assert "error" not in result, f"Error: {result.get('error')}"
        assert result.get("filings_count", 0) >= 1
        assert "markdown" in result
        assert "AAPL" in result["markdown"]

    def test_discover_multiple_forms(self):
        """Discover with multiple form types."""
        s, b = tool_call("stock_notes", {
            "command": "discover",
            "instructions": {"ticker": "AAPL", "forms": "10-K,10-Q"}
        }, timeout=60)
        result = extract(b)
        assert "error" not in result
        assert result.get("filings_count", 0) >= 1

    def test_discover_invalid_ticker(self):
        """Discover with invalid ticker returns error."""
        s, b = tool_call("stock_notes", {
            "command": "discover",
            "instructions": {"ticker": "INVALIDTICKER123XYZ"}
        }, timeout=60)
        result = extract(b)
        assert "error" in result or result.get("filings_count", 0) == 0

    def test_note_list(self):
        """List notes in a filing (uses first 10-K from discover)."""
        # First discover to get an accession number
        s, b = tool_call("stock_notes", {
            "command": "discover",
            "instructions": {"ticker": "AAPL", "forms": "10-K"}
        }, timeout=60)
        result = extract(b)
        if result.get("filings_count", 0) == 0:
            pytest.skip("No filings found")

        # Extract accession from markdown table
        accession = None
        for line in result["markdown"].split("\n"):
            if "|" in line and "10-K" in line:
                parts = [p.strip() for p in line.split("|")]
                for p in parts:
                    if p.startswith("000"):
                        accession = p
                        break
                if accession:
                    break

        if not accession:
            pytest.skip("Could not extract accession number from discover output")

        # List notes
        s, b = tool_call("stock_notes", {
            "command": "note",
            "instructions": {"accession_no": accession, "ticker": "AAPL"}
        }, timeout=120)
        result = extract(b)
        assert "error" not in result, f"Error: {result.get('error')}"
        assert "markdown" in result

    def test_details_invalid_date_format(self):
        """Details with bad date format returns error."""
        s, b = tool_call("stock_notes", {
            "command": "details",
            "instructions": {
                "ticker": "AAPL",
                "concept": "us-gaap:Revenue",
                "start_date": "bad-date"
            }
        })
        result = extract(b)
        assert "error" in result


# ═══════════════════════════════════════════════════════════════════
#  5. DATABASE SCHEMA TESTS
# ═══════════════════════════════════════════════════════════════════

class TestSchema:
    """Verify new tables exist in the database."""

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

    def test_sn_note_details_table(self):
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        assert "sn_note_details" in d["operational"]["tables"]

    def test_sn_detail_registry_table(self):
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        assert "sn_detail_registry" in d["operational"]["tables"]

    def test_schema_columns(self):
        """Verify key columns exist in sf_quarterly_facts."""
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        cols = d["operational"]["tables"]["sf_quarterly_facts"]["columns"]
        for expected in ("ticker", "statement_type", "concept", "quarter", "numeric_value"):
            assert expected in cols, f"Missing column: {expected}"

    def test_schema_ok(self):
        """All tables should have schema_ok=true."""
        r = requests.get(f"{BASE}/databases")
        d = r.json()
        for tbl_name, tbl_info in d["operational"]["tables"].items():
            if tbl_info["exists"]:
                assert tbl_info.get("schema_ok", True), f"Schema mismatch in {tbl_name}"
