#!/usr/bin/env python3
"""
Comprehensive test suite for Activity Server.

Covers:
  - Platform config (.env SERVER_PLATFORM)
  - All HTTP endpoints (health, databases, sync, resolve, telemetry, artifacts, runs)
  - MCP protocol (initialize, tools/list, tools/call)
  - All 8 tools with valid inputs
  - Edge cases: injection, missing params, unknown tools, timeouts, empty input
  - Observability header (X-Observe)
  - Tool docs endpoint
  - Sync and resolve flows

Usage:
  pytest test_comprehensive.py -v
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

# ── Server management ────────────────────────────────────────────────

BASE = "http://127.0.0.1:8082"
_proc = None


def _start_server():
    """Start server subprocess, wait until healthy."""
    global _proc
    data_dir = Path(__file__).parent / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)

    env = os.environ.copy()
    env["SERVER_PLATFORM"] = "linux"

    _proc = subprocess.Popen(
        [sys.executable, "run.py", "--port", "8082", "--resolve-operational"],
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
        _proc = None


@pytest.fixture(scope="session", autouse=True)
def server():
    # If server is already running (started externally), skip startup
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

def mcp(method, params=None, req_id=1):
    """Send MCP JSON-RPC request."""
    r = requests.post(f"{BASE}/mcp", json={
        "jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {},
    })
    return r.status_code, r.json()


def tool_call(tool_name, arguments=None, observe=False, timeout=None):
    """Call a tool via MCP. Returns (status, body)."""
    headers = {"X-Observe": "true"} if observe else {}
    params = {}
    if timeout:
        params["timeout"] = str(timeout)
    r = requests.post(f"{BASE}/mcp", params=params, headers=headers, json={
        "jsonrpc": "2.0", "id": 99, "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments or {}},
    })
    return r.status_code, r.json()


def extract(body):
    """Extract parsed tool result from MCP response.
    Returns the parsed JSON from the tool's text content.
    If the MCP response is a JSON-RPC error, returns {"error": message}.
    """
    if "error" in body:
        return {"error": body["error"].get("message", str(body["error"]))}
    return json.loads(body["result"]["content"][0]["text"])


# ═══════════════════════════════════════════════════════════════════
#  1. PLATFORM CONFIG
# ═══════════════════════════════════════════════════════════════════

class TestPlatformConfig:
    def test_env_file_exists(self):
        assert (Path(__file__).parent / ".env").exists()

    def test_env_has_platform(self):
        content = (Path(__file__).parent / ".env").read_text()
        assert "SERVER_PLATFORM" in content

    def test_config_loader_exposes_platform(self):
        from server.config.loader import PLATFORM
        assert PLATFORM in ("windows", "linux")

    def test_platform_auto_detect(self):
        """PLATFORM should resolve to a known value even when .env is empty."""
        from server.config import loader
        assert loader.PLATFORM in ("windows", "linux")

    def test_platform_override(self):
        """Explicit SERVER_PLATFORM=linux should be honoured."""
        from server.config.loader import PLATFORM
        # We set SERVER_PLATFORM=linux in the test env
        assert PLATFORM == "linux"


# ═══════════════════════════════════════════════════════════════════
#  2. HTTP ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

class TestHTTPEndpoints:
    def test_health(self):
        r = requests.get(f"{BASE}/health")
        assert r.status_code == 200
        d = r.json()
        assert d["status"] in ("healthy", "degraded")
        assert len(d["tools"]) == 10

    def test_root_returns_health(self):
        r = requests.get(f"{BASE}/")
        assert r.status_code == 200
        assert "status" in r.json()

    def test_databases(self):
        r = requests.get(f"{BASE}/databases")
        assert r.status_code == 200
        d = r.json()
        for key in ("operational", "cloud", "telemetry", "in_sync"):
            assert key in d
        op = d["operational"]["tables"]
        for tbl in ("artifacts", "tool_runs", "tools"):
            assert tbl in op

    def test_sync(self):
        r = requests.get(f"{BASE}/sync")
        assert r.status_code == 200
        d = r.json()
        assert "in_sync" in d
        assert "tables" in d

    def test_resolve_operational(self):
        r = requests.post(f"{BASE}/resolve", json={"source": "operational"})
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "resolved"
        assert d["source"] == "operational"

    def test_resolve_cloud(self):
        r = requests.post(f"{BASE}/resolve", json={"source": "cloud"})
        assert r.status_code == 200
        assert r.json()["source"] == "cloud"

    def test_resolve_invalid_source(self):
        r = requests.post(f"{BASE}/resolve", json={"source": "bogus"})
        assert r.status_code == 400

    def test_resolve_missing_source(self):
        r = requests.post(f"{BASE}/resolve", json={})
        assert r.status_code == 400

    def test_telemetry(self):
        r = requests.get(f"{BASE}/telemetry")
        assert r.status_code == 200
        d = r.json()
        assert "count" in d
        assert "entries" in d

    def test_telemetry_limit(self):
        r = requests.get(f"{BASE}/telemetry?limit=2")
        assert r.status_code == 200
        assert r.json()["count"] <= 2

    def test_artifacts(self):
        r = requests.get(f"{BASE}/artifacts")
        assert r.status_code == 200
        d = r.json()
        assert "count" in d
        assert "artifacts" in d

    def test_runs(self):
        r = requests.get(f"{BASE}/runs")
        assert r.status_code == 200
        assert "runs" in r.json()

    def test_runs_filter(self):
        r = requests.get(f"{BASE}/runs?tool=calculator&limit=5")
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════
#  3. MCP PROTOCOL
# ═══════════════════════════════════════════════════════════════════

class TestMCPProtocol:
    def test_initialize(self):
        s, b = mcp("initialize", req_id=1)
        assert s == 200
        assert b["jsonrpc"] == "2.0"
        info = b["result"]["serverInfo"]
        assert info["name"] == "activity-server"

    def test_tools_list(self):
        s, b = mcp("tools/list", req_id=2)
        names = {t["name"] for t in b["result"]["tools"]}
        expected = {"artifact_saver", "calculator", "crypto", "db_writer",
                    "healthcheck", "slow_hello", "stock_financials", "stock_notes",
                    "textstats", "timestamp"}
        assert names == expected
        for t in b["result"]["tools"]:
            assert "inputSchema" in t

    def test_unknown_method(self):
        _, b = mcp("bogus", req_id=3)
        assert b["error"]["code"] == -32601

    def test_malformed_json(self):
        r = requests.post(f"{BASE}/mcp", data="not json")
        assert r.json()["error"]["code"] == -32700

    def test_missing_method(self):
        _, b = mcp("", req_id=4)
        assert "error" in b


# ═══════════════════════════════════════════════════════════════════
#  4. CALCULATOR
# ═══════════════════════════════════════════════════════════════════

class TestCalculator:
    def test_basic(self):
        assert extract(tool_call("calculator", {"expression": "2+3"})[1])["result"] == 5

    def test_complex(self):
        r = extract(tool_call("calculator", {"expression": "2 + 3 * sqrt(16)"})[1])
        assert r["result"] == 14.0

    def test_trig(self):
        assert extract(tool_call("calculator", {"expression": "sin(0)"})[1])["result"] == 0.0

    def test_pi(self):
        r = extract(tool_call("calculator", {"expression": "pi"})[1])
        assert 3.14 < r["result"] < 3.15

    def test_power(self):
        assert extract(tool_call("calculator", {"expression": "2**10"})[1])["result"] == 1024

    def test_nested(self):
        assert extract(tool_call("calculator", {"expression": "sqrt(abs(-16))"})[1])["result"] == 4.0

    def test_neg_numbers(self):
        assert extract(tool_call("calculator", {"expression": "abs(-42)"})[1])["result"] == 42

    def test_float_precision(self):
        r = extract(tool_call("calculator", {"expression": "0.1 + 0.2"})[1])
        assert abs(r["result"] - 0.3) < 1e-10

    def test_division_by_zero(self):
        r = extract(tool_call("calculator", {"expression": "1/0"})[1])
        assert "error" in r

    def test_missing_expression(self):
        s, b = tool_call("calculator", {})
        assert s == 500 or "error" in b

    def test_empty_expression(self):
        _, b = tool_call("calculator", {"expression": ""})
        r = extract(b)
        assert "result" in r or "error" in r

    # ── Injection tests ──
    def test_inject_os_system(self):
        r = extract(tool_call("calculator", {"expression": "__import__('os').system('id')"})[1])
        assert "error" in r or "Forbidden" in str(r)

    def test_inject_eval(self):
        r = extract(tool_call("calculator", {"expression": "eval('1+1')"})[1])
        assert "error" in r or "Forbidden" in str(r)

    def test_inject_exec(self):
        r = extract(tool_call("calculator", {"expression": "exec('pass')"})[1])
        assert "error" in r or "Forbidden" in str(r)

    def test_inject_dunder(self):
        r = extract(tool_call("calculator", {"expression": "__class__.__bases__"})[1])
        assert "error" in r or "Forbidden" in str(r)

    def test_inject_open(self):
        r = extract(tool_call("calculator", {"expression": "open('/etc/passwd')"})[1])
        assert "error" in r or "Forbidden" in str(r)

    def test_inject_lambda(self):
        r = extract(tool_call("calculator", {"expression": "lambda: os.system('x')"})[1])
        assert "error" in r or "Forbidden" in str(r)

    def test_inject_subprocess(self):
        r = extract(tool_call("calculator", {"expression": "subprocess.call(['ls'])"})[1])
        assert "error" in r or "Forbidden" in str(r)


# ═══════════════════════════════════════════════════════════════════
#  5. CRYPTO
# ═══════════════════════════════════════════════════════════════════

class TestCrypto:
    def test_basic(self):
        r = extract(tool_call("crypto", {"plaintext": "hello"})[1])
        assert len(r["encrypted"]) == 64
        assert r["algorithm"] == "sha256-hex"

    def test_deterministic(self):
        r1 = extract(tool_call("crypto", {"plaintext": "test"})[1])
        r2 = extract(tool_call("crypto", {"plaintext": "test"})[1])
        assert r1["encrypted"] == r2["encrypted"]

    def test_custom_salt(self):
        r1 = extract(tool_call("crypto", {"plaintext": "hi", "salt": "a"})[1])
        r2 = extract(tool_call("crypto", {"plaintext": "hi", "salt": "b"})[1])
        assert r1["encrypted"] != r2["encrypted"]

    def test_empty(self):
        r = extract(tool_call("crypto", {"plaintext": ""})[1])
        assert len(r["encrypted"]) == 64

    def test_unicode(self):
        r = extract(tool_call("crypto", {"plaintext": "你好 🌍"})[1])
        assert len(r["encrypted"]) == 64

    def test_long_input(self):
        r = extract(tool_call("crypto", {"plaintext": "A" * 100_000})[1])
        assert len(r["encrypted"]) == 64

    def test_missing_plaintext(self):
        s, b = tool_call("crypto", {})
        assert s == 500 or "error" in b


# ═══════════════════════════════════════════════════════════════════
#  6. TIMESTAMP
# ═══════════════════════════════════════════════════════════════════

class TestTimestamp:
    def test_iso_and_epoch(self):
        r = extract(tool_call("timestamp", {})[1])
        assert "iso" in r and "T" in r["iso"]
        assert r["epoch"] > 0

    def test_epoch_recent(self):
        r = extract(tool_call("timestamp", {})[1])
        assert abs(r["epoch"] - time.time()) < 5


# ═══════════════════════════════════════════════════════════════════
#  7. TEXTSTATS
# ═══════════════════════════════════════════════════════════════════

class TestTextStats:
    def test_basic(self):
        r = extract(tool_call("textstats", {"text": "The quick brown fox."})[1])
        assert r["word_count"] == 4
        assert r["char_count"] == 20
        assert len(r["sha256"]) == 64

    def test_single_word(self):
        assert extract(tool_call("textstats", {"text": "hello"})[1])["word_count"] == 1

    def test_empty(self):
        r = extract(tool_call("textstats", {"text": ""})[1])
        assert r["word_count"] == 0 and r["char_count"] == 0

    def test_multiline(self):
        r = extract(tool_call("textstats", {"text": "A.\nB.\nC."})[1])
        assert r["sentence_count"] == 3

    def test_unicode(self):
        assert extract(tool_call("textstats", {"text": "你好 世界"})[1])["word_count"] == 2

    def test_large(self):
        assert extract(tool_call("textstats", {"text": "w " * 10_000})[1])["word_count"] == 10_000

    def test_missing(self):
        s, b = tool_call("textstats", {})
        assert s == 500 or "error" in b

    def test_whitespace_only(self):
        assert extract(tool_call("textstats", {"text": "  \n\t "})[1])["word_count"] == 0


# ═══════════════════════════════════════════════════════════════════
#  8. HEALTHCHECK
# ═══════════════════════════════════════════════════════════════════

class TestHealthcheck:
    def test_tool(self):
        r = extract(tool_call("healthcheck", {})[1])
        assert r["status"] in ("healthy", "degraded")
        assert len(r["registered_tools"]) == 10
        assert "artifacts" in r["sync_status"]

    def test_sync_info(self):
        r = extract(tool_call("healthcheck", {})[1])
        assert "operational_counts" in r


# ═══════════════════════════════════════════════════════════════════
#  9. DB_WRITER
# ═══════════════════════════════════════════════════════════════════

class TestDBWriter:
    def test_basic(self):
        r = extract(tool_call("db_writer", {"data": {"k": "v"}, "label": "t"})[1])
        assert r["saved"] and r["db_row_id"] > 0

    def test_empty_data(self):
        r = extract(tool_call("db_writer", {"data": {}})[1])
        assert r["saved"] and r["record_count"] == 0

    def test_nested(self):
        r = extract(tool_call("db_writer", {"data": {"n": {"a": [1, 2]}}})[1])
        assert r["saved"]

    def test_missing_data(self):
        s, b = tool_call("db_writer", {})
        assert s == 500 or "error" in b

    def test_shows_in_runs(self):
        tool_call("db_writer", {"data": {"check": 1}})
        d = requests.get(f"{BASE}/runs?tool=db_writer&limit=1").json()
        assert d["count"] >= 1


# ═══════════════════════════════════════════════════════════════════
#  10. ARTIFACT_SAVER
# ═══════════════════════════════════════════════════════════════════

class TestArtifactSaver:
    def test_save(self):
        r = extract(tool_call("artifact_saver", {"content": "hi", "filename": "t1"})[1])
        assert r["saved"] and r["filename"] == "t1.txt"
        assert os.path.exists(r["filepath"])

    def test_auto_filename(self):
        r = extract(tool_call("artifact_saver", {"content": "auto"})[1])
        assert r["filename"].startswith("artifact_")

    def test_txt_added(self):
        r = extract(tool_call("artifact_saver", {"content": "x", "filename": "noext"})[1])
        assert r["filename"] == "noext.txt"

    def test_content_preview(self):
        r = extract(tool_call("artifact_saver", {"content": "X" * 500, "filename": "pv"})[1])
        assert len(r["content_preview"]) <= 200

    def test_unicode(self):
        r = extract(tool_call("artifact_saver", {"content": "你好 🌍", "filename": "uni"})[1])
        assert r["saved"]

    def test_empty_content(self):
        r = extract(tool_call("artifact_saver", {"content": "", "filename": "empty"})[1])
        assert r["saved"] and r["size_bytes"] == 0

    def test_missing_content(self):
        s, b = tool_call("artifact_saver", {})
        assert s == 500 or "error" in b

    def test_appears_in_artifacts(self):
        tool_call("artifact_saver", {"content": "ep", "filename": "ep_check"})
        d = requests.get(f"{BASE}/artifacts?limit=5").json()
        assert d["count"] >= 1


# ═══════════════════════════════════════════════════════════════════
#  11. SLOW_HELLO
# ═══════════════════════════════════════════════════════════════════

class TestSlowHello:
    def test_short_delay(self):
        r = extract(tool_call("slow_hello", {"delay": 1}, timeout=10)[1])
        assert r["message"] == "Hello World" and r["delay_seconds"] == 1

    def test_zero_delay(self):
        r = extract(tool_call("slow_hello", {"delay": 0}, timeout=10)[1])
        assert r["delay_seconds"] == 0

    def test_negative_clamped(self):
        r = extract(tool_call("slow_hello", {"delay": -5}, timeout=10)[1])
        assert r["delay_seconds"] == 0

    def test_timeout(self):
        s, b = tool_call("slow_hello", {"delay": 30}, timeout=2)
        assert s == 504 or (s == 200 and "error" in str(b))


# ═══════════════════════════════════════════════════════════════════
#  12. HTTP TOOL ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

class TestHTTPToolEndpoints:
    def test_post_tool(self):
        r = requests.post(f"{BASE}/tools/calculator",
                          json={"arguments": {"expression": "10*5+2"}})
        assert r.status_code == 200
        assert r.json()["result"]["result"] == 52

    def test_unknown_tool(self):
        assert requests.post(f"{BASE}/tools/nope", json={"arguments": {}}).status_code == 404

    def test_missing_args(self):
        r = requests.post(f"{BASE}/tools/calculator", json={})
        assert r.status_code in (200, 500)

    def test_docs_markdown(self):
        r = requests.get(f"{BASE}/tools/calculator/docs")
        assert r.status_code == 200 and "calculator" in r.text.lower()

    def test_docs_json(self):
        r = requests.get(f"{BASE}/tools/crypto/docs",
                         headers={"Accept": "application/json"})
        assert r.status_code == 200 and "description" in r.json()

    def test_docs_unknown(self):
        assert requests.get(f"{BASE}/tools/nope/docs").status_code == 404


# ═══════════════════════════════════════════════════════════════════
#  13. OBSERVABILITY
# ═══════════════════════════════════════════════════════════════════

class TestObservability:
    def test_observe_on(self):
        _, b = tool_call("timestamp", {}, observe=True)
        lineage = b["result"]["lineage"]
        assert len(lineage) >= 1
        for e in lineage:
            assert "name" in e and "ok" in e and "duration_ms" in e

    def test_observe_off(self):
        _, b = tool_call("timestamp", {}, observe=False)
        assert "lineage" not in b["result"]

    def test_observe_http(self):
        r = requests.post(f"{BASE}/tools/timestamp",
                          headers={"X-Observe": "true"}, json={"arguments": {}})
        assert "lineage" in r.json()

    def test_multi_activity(self):
        """Calculator has sanitize + evaluate — lineage should capture both."""
        _, b = tool_call("calculator", {"expression": "1+1"}, observe=True)
        names = [e["name"] for e in b["result"]["lineage"]]
        assert "calculator.sanitize" in names and "calculator.evaluate" in names

    def test_error_in_lineage(self):
        _, b = tool_call("calculator", {"expression": "1/0"}, observe=True)
        # Error should be captured
        assert "error" in str(b)


# ═══════════════════════════════════════════════════════════════════
#  14. EDGE CASES
# ═══════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_unknown_tool_mcp(self):
        _, b = tool_call("does_not_exist", {})
        assert b["error"]["code"] == -32601

    def test_unknown_tool_http(self):
        assert requests.post(f"{BASE}/tools/nope", json={"arguments": {}}).status_code == 404

    def test_empty_body_http(self):
        r = requests.post(f"{BASE}/tools/calculator")
        assert r.status_code in (200, 400, 500)

    def test_concurrent(self):
        """10 concurrent requests — no crashes."""
        import concurrent.futures
        def call(i):
            return extract(tool_call("calculator", {"expression": f"{i}*2"})[1])["result"]
        with concurrent.futures.ThreadPoolExecutor(10) as ex:
            results = list(ex.map(call, range(10)))
        assert results == [i * 2 for i in range(10)]

    def test_rapid_sequential(self):
        for _ in range(20):
            r = extract(tool_call("timestamp", {})[1])
            assert "iso" in r

    def test_xss_in_args(self):
        r = extract(tool_call("crypto", {"plaintext": "<script>alert(1)</script>"})[1])
        assert len(r["encrypted"]) == 64

    def test_extra_args_ignored(self):
        r = extract(tool_call("timestamp", {"bogus": 1})[1])
        assert "iso" in r

    def test_sync_after_ops(self):
        tool_call("artifact_saver", {"content": "sync", "filename": "sync_test"})
        d = requests.get(f"{BASE}/sync").json()
        assert "in_sync" in d

    def test_resolve_then_tool(self):
        requests.post(f"{BASE}/resolve", json={"source": "operational"})
        r = extract(tool_call("calculator", {"expression": "1+1"})[1])
        assert r["result"] == 2


# ═══════════════════════════════════════════════════════════════════
#  15. DATA FLOW E2E
# ═══════════════════════════════════════════════════════════════════

class TestDataFlow:
    def test_artifact_synced(self):
        tool_call("artifact_saver", {"content": "cloud", "filename": "cloud_sync"})
        d = requests.get(f"{BASE}/sync").json()
        assert "artifacts" in d["tables"]

    def test_run_recorded(self):
        tool_call("calculator", {"expression": "99"})
        d = requests.get(f"{BASE}/runs?limit=5").json()
        assert d["count"] >= 1
        assert any(r.get("tool_name") == "calculator" for r in d["runs"])

    def test_telemetry_grows(self):
        before = requests.get(f"{BASE}/telemetry?limit=1").json()["count"]
        tool_call("timestamp", {})
        after = requests.get(f"{BASE}/telemetry?limit=100").json()["count"]
        assert after >= before
