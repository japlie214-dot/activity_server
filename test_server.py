#!/usr/bin/env python3
"""
Integration test for Activity Server.
Tests: health, sync, MCP, all 8 tools, observability header, db_writer, artifact_saver.

Usage:
  python run.py --port 8080 &
  sleep 3
  python test_server.py
"""
import asyncio
import json
import os
import sys
import aiohttp

BASE = "http://127.0.0.1:8080"


async def test():
    async with aiohttp.ClientSession() as s:
        print("\n" + "=" * 60)
        print("TEST: Activity Server Integration")
        print("=" * 60)

        # 1. Health
        print("\n[1] GET /health")
        async with s.get(f"{BASE}/health") as r:
            assert r.status == 200
            data = await r.json()
            print(f"    Status: {data['status']}, Tools: {len(data['tools'])}")
            assert data["status"] in ("healthy", "degraded")
            assert len(data["tools"]) == 8

        # 2. Sync
        print("\n[2] GET /sync")
        async with s.get(f"{BASE}/sync") as r:
            assert r.status == 200
            data = await r.json()
            print(f"    In sync: {data['in_sync']}")

        # 3. MCP initialize
        print("\n[3] MCP initialize")
        async with s.post(f"{BASE}/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}
        }) as r:
            data = await r.json()
            print(f"    Server: {data['result']['serverInfo']['name']}")

        # 4. MCP tools/list
        print("\n[4] MCP tools/list")
        async with s.post(f"{BASE}/mcp", json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}
        }) as r:
            data = await r.json()
            tools = data["result"]["tools"]
            print(f"    Found {len(tools)} tools")
            assert len(tools) == 8

        # 5. Calculator
        print("\n[5] Calculator")
        async with s.post(f"{BASE}/mcp", json={
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "calculator", "arguments": {"expression": "2 + 3 * sqrt(16)"}}
        }) as r:
            data = await r.json()
            result = json.loads(data["result"]["content"][0]["text"])
            print(f"    2 + 3 * sqrt(16) = {result['result']}")
            assert result["result"] == 14.0

        # 6. Crypto
        print("\n[6] Crypto")
        async with s.post(f"{BASE}/mcp", json={
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "crypto", "arguments": {"plaintext": "hello"}}
        }) as r:
            data = await r.json()
            result = json.loads(data["result"]["content"][0]["text"])
            print(f"    Encrypted: {result['encrypted'][:32]}...")
            assert len(result["encrypted"]) == 64

        # 7. HealthCheck
        print("\n[7] HealthCheck")
        async with s.post(f"{BASE}/mcp", json={
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "healthcheck", "arguments": {}}
        }) as r:
            data = await r.json()
            result = json.loads(data["result"]["content"][0]["text"])
            print(f"    Status: {result['status']}")

        # 8. Timestamp
        print("\n[8] Timestamp")
        async with s.post(f"{BASE}/mcp", json={
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "timestamp", "arguments": {}}
        }) as r:
            data = await r.json()
            result = json.loads(data["result"]["content"][0]["text"])
            print(f"    UTC: {result['iso']}")

        # 9. TextStats
        print("\n[9] TextStats")
        async with s.post(f"{BASE}/mcp", json={
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "textstats", "arguments": {"text": "The quick brown fox."}}
        }) as r:
            data = await r.json()
            result = json.loads(data["result"]["content"][0]["text"])
            print(f"    Words: {result['word_count']}, Chars: {result['char_count']}")

        # 10. DB Writer (writes to operational DB)
        print("\n[10] DB Writer")
        async with s.post(f"{BASE}/mcp", json={
            "jsonrpc": "2.0", "id": 8, "method": "tools/call",
            "params": {"name": "db_writer", "arguments": {
                "data": {"test_key": "test_value", "count": 42},
                "label": "integration_test",
            }}
        }) as r:
            data = await r.json()
            result = json.loads(data["result"]["content"][0]["text"])
            print(f"    Saved: {result['saved']}, Row ID: {result['db_row_id']}")
            assert result["saved"] is True

        # 11. Artifact Saver (writes .txt file + DB record)
        print("\n[11] Artifact Saver")
        async with s.post(f"{BASE}/mcp", json={
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "artifact_saver", "arguments": {
                "content": "This is a test artifact.\nLine 2.\nLine 3.",
                "filename": "test_output",
            }}
        }) as r:
            data = await r.json()
            result = json.loads(data["result"]["content"][0]["text"])
            print(f"    File: {result['filename']}, Size: {result['size_bytes']} bytes")
            assert result["saved"] is True
            assert os.path.exists(result["filepath"])

        # 12. Observability header
        print("\n[12] Observability header")
        async with s.post(f"{BASE}/mcp",
                          headers={"X-Observability": "true"},
                          json={
                              "jsonrpc": "2.0", "id": 10, "method": "tools/call",
                              "params": {"name": "timestamp", "arguments": {}}
                          }) as r:
            data = await r.json()
            assert "observability_report" in data["result"]
            report = data["result"]["observability_report"]
            print(f"    Activities captured: {len(report)}")
            for entry in report:
                print(f"      • {entry['activity']} ({entry['duration_ms']}ms)")

        # 13. Observability off (default)
        print("\n[13] Observability off (default)")
        async with s.post(f"{BASE}/mcp", json={
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {"name": "timestamp", "arguments": {}}
        }) as r:
            data = await r.json()
            assert "observability_report" not in data["result"]
            print("    ✓ No observability report (as expected)")

        # 14. Injection prevention
        print("\n[14] Injection prevention")
        async with s.post(f"{BASE}/mcp", json={
            "jsonrpc": "2.0", "id": 12, "method": "tools/call",
            "params": {"name": "calculator", "arguments": {
                "expression": "__import__('os').system('echo hacked')"
            }}
        }) as r:
            data = await r.json()
            result = json.loads(data["result"]["content"][0]["text"])
            assert "error" in result or "Forbidden" in str(result)
            print("    ✓ Injection blocked")

        # 15. Slow hello with short delay (to test long polling mechanism)
        print("\n[15] Slow Hello (short delay=2 for testing)")
        async with s.post(f"{BASE}/mcp",
                          params={"timeout": "10"},
                          json={
                              "jsonrpc": "2.0", "id": 13, "method": "tools/call",
                              "params": {"name": "slow_hello", "arguments": {"delay": 2}}
                          }) as r:
            assert r.status == 200
            data = await r.json()
            result = json.loads(data["result"]["content"][0]["text"])
            print(f"    Message: {result['message']}, Delay: {result['delay_seconds']}s")

        # 16. Telemetry
        print("\n[16] GET /telemetry")
        async with s.get(f"{BASE}/telemetry?limit=5") as r:
            data = await r.json()
            print(f"    Entries: {data['count']}")

        # 17. HTTP tool run endpoint
        print("\n[17] POST /tools/calculator")
        async with s.post(f"{BASE}/tools/calculator",
                          json={"arguments": {"expression": "10 * 5 + 2"}}) as r:
            data = await r.json()
            print(f"    Result: {data['result']['result']}")
            assert data["result"]["result"] == 52

        # 18. HTTP tool run with observability
        print("\n[18] POST /tools/crypto with observability")
        async with s.post(f"{BASE}/tools/crypto",
                          headers={"X-Observability": "true"},
                          json={"arguments": {"plaintext": "test"}}) as r:
            data = await r.json()
            assert "observability_report" in data
            print(f"    Activities: {len(data['observability_report'])}")

        print("\n" + "=" * 60)
        print("ALL TESTS PASSED ✓")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test())
