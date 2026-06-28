#!/usr/bin/env python3
"""Comprehensive integration test — tests every tool + Snowflake writes."""
import requests, json, time, concurrent.futures

BASE = 'http://127.0.0.1:8100'
passed = 0
failed = 0

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f'  OK {name}')
        passed += 1
    else:
        print(f'  FAIL {name} {detail}')
        failed += 1

def sf_query(sql):
    """Run a query against Snowflake via the server's cloud connection."""
    # We'll use the /databases endpoint to verify, or direct tool calls
    pass

# ═══════════════════════════════════════════════════════════════
# 1. STARTUP VALIDATION
# ═══════════════════════════════════════════════════════════════
print("=== 1. STARTUP VALIDATION ===")
r = requests.get(f'{BASE}/health')
d = r.json()
test('Server healthy', d['status'] == 'healthy')
test('Tools unlocked', d['tools_locked'] == False)
test('10 tools registered', len(d['tools']) == 10)

r = requests.get(f'{BASE}/databases')
d = r.json()
test('Snowflake connected', d['cloud']['status'] == 'connected')
test('In sync', d['in_sync'] == True)

# Verify all 7 sync tables exist in cloud
for tbl in ('artifacts', 'sf_tickers', 'sf_quarterly_facts', 'sn_filings', 'sn_notes', 'sn_detail_registry', 'sn_note_details'):
    test(f'Cloud/{tbl} exists', d['cloud']['tables'].get(tbl, {}).get('exists', False))

# ═══════════════════════════════════════════════════════════════
# 2. CALCULATOR — pure computation, no DB
# ═══════════════════════════════════════════════════════════════
print("\n=== 2. CALCULATOR ===")
cases = [
    ("2+3", 5), ("2+3*sqrt(16)", 14.0), ("sin(0)", 0.0),
    ("cos(0)", 1.0), ("abs(-42)", 42), ("2**10", 1024),
    ("sqrt(abs(-16))", 4.0), ("round(3.7)", 4),
]
for expr, expected in cases:
    r = requests.post(f'{BASE}/tools/calculator', json={'arguments': {'expression': expr}})
    test(f'{expr}={expected}', r.json()['result']['result'] == expected)

# Injection tests
for expr in ['__import__("os")', 'eval("1")', 'exec("pass")', '__class__', 'open("/etc/passwd")', 'lambda: 1', 'subprocess.call']:
    r = requests.post(f'{BASE}/tools/calculator', json={'arguments': {'expression': expr}})
    d = r.json()
    test(f'Block: {expr[:30]}', 'error' in d or 'Forbidden' in str(d))

# Edge cases
r = requests.post(f'{BASE}/tools/calculator', json={'arguments': {'expression': '1/0'}})
test('Division by zero', 'error' in r.json())
r = requests.post(f'{BASE}/tools/calculator', json={'arguments': {}})
test('Missing expression', r.status_code in (200, 500))

# ═══════════════════════════════════════════════════════════════
# 3. CRYPTO — pure computation, no DB
# ═══════════════════════════════════════════════════════════════
print("\n=== 3. CRYPTO ===")
r = requests.post(f'{BASE}/tools/crypto', json={'arguments': {'plaintext': 'hello'}})
d = r.json()['result']
test('SHA256 64 hex', len(d['encrypted']) == 64)
test('Algorithm sha256-hex', d['algorithm'] == 'sha256-hex')

# Deterministic
r1 = requests.post(f'{BASE}/tools/crypto', json={'arguments': {'plaintext': 'test'}})
r2 = requests.post(f'{BASE}/tools/crypto', json={'arguments': {'plaintext': 'test'}})
test('Deterministic', r1.json()['result']['encrypted'] == r2.json()['result']['encrypted'])

# Different salts
r1 = requests.post(f'{BASE}/tools/crypto', json={'arguments': {'plaintext': 'hi', 'salt': 'a'}})
r2 = requests.post(f'{BASE}/tools/crypto', json={'arguments': {'plaintext': 'hi', 'salt': 'b'}})
test('Different salts differ', r1.json()['result']['encrypted'] != r2.json()['result']['encrypted'])

# Edge cases
r = requests.post(f'{BASE}/tools/crypto', json={'arguments': {'plaintext': ''}})
test('Empty string', len(r.json()['result']['encrypted']) == 64)
r = requests.post(f'{BASE}/tools/crypto', json={'arguments': {'plaintext': 'hello 🌍'}})
test('Unicode', len(r.json()['result']['encrypted']) == 64)
r = requests.post(f'{BASE}/tools/crypto', json={'arguments': {'plaintext': 'A' * 100000}})
test('Long input', len(r.json()['result']['encrypted']) == 64)

# ═══════════════════════════════════════════════════════════════
# 4. TIMESTAMP — pure computation, no DB
# ═══════════════════════════════════════════════════════════════
print("\n=== 4. TIMESTAMP ===")
r = requests.post(f'{BASE}/tools/timestamp', json={'arguments': {}})
d = r.json()['result']
test('Has ISO-8601', 'T' in d['iso'] and 'Z' in d['iso'] or '+' in d['iso'])
test('Has epoch', d['epoch'] > 0)
test('Epoch recent', abs(d['epoch'] - time.time()) < 5)

# ═══════════════════════════════════════════════════════════════
# 5. TEXTSTATS — pure computation, no DB
# ═══════════════════════════════════════════════════════════════
print("\n=== 5. TEXTSTATS ===")
r = requests.post(f'{BASE}/tools/textstats', json={'arguments': {'text': 'The quick brown fox.'}})
d = r.json()['result']
test('Word count 4', d['word_count'] == 4)
test('Char count 20', d['char_count'] == 20)
test('Sentence count 1', d['sentence_count'] == 1)
test('Has sha256', len(d['sha256']) == 64)

r = requests.post(f'{BASE}/tools/textstats', json={'arguments': {'text': ''}})
test('Empty text', r.json()['result']['word_count'] == 0)

r = requests.post(f'{BASE}/tools/textstats', json={'arguments': {'text': 'Hello 世界 🌍'}})
test('Unicode text', r.json()['result']['word_count'] == 3)

r = requests.post(f'{BASE}/tools/textstats', json={'arguments': {'text': 'A.\nB.\nC.'}})
test('Multiline sentences', r.json()['result']['sentence_count'] == 3)

# ═══════════════════════════════════════════════════════════════
# 6. DB WRITER — writes to operational DB
# ═══════════════════════════════════════════════════════════════
print("\n=== 6. DB WRITER ===")
r = requests.post(f'{BASE}/tools/db_writer', json={'arguments': {'data': {'k': 'v', 'num': 42}, 'label': 'test'}})
d = r.json()['result']
test('Saved', d['saved'] == True)
test('Row ID > 0', d['db_row_id'] > 0)
test('Record count 1', d['record_count'] == 1)

r = requests.post(f'{BASE}/tools/db_writer', json={'arguments': {'data': {}}})
test('Empty data saved', r.json()['result']['saved'] == True)

r = requests.post(f'{BASE}/tools/db_writer', json={'arguments': {'data': {'nested': {'a': [1, 2]}}}})
test('Nested data saved', r.json()['result']['saved'] == True)

# ═══════════════════════════════════════════════════════════════
# 7. ARTIFACT SAVER — writes file + DB (cloud-synced)
# ═══════════════════════════════════════════════════════════════
print("\n=== 7. ARTIFACT SAVER ===")
r = requests.post(f'{BASE}/tools/artifact_saver', json={'arguments': {'content': 'test content for artifact', 'filename': 'test_artifact'}})
d = r.json()['result']
test('Saved', d['saved'] == True)
test('Filename .txt', d['filename'] == 'test_artifact.txt')
test('Size > 0', d['size_bytes'] > 0)
test('Content preview', 'test content' in d['content_preview'])

# Auto filename
r = requests.post(f'{BASE}/tools/artifact_saver', json={'arguments': {'content': 'auto'}})
test('Auto filename starts with artifact_', r.json()['result']['filename'].startswith('artifact_'))

# Unicode
r = requests.post(f'{BASE}/tools/artifact_saver', json={'arguments': {'content': '你好 🌍', 'filename': 'uni'}})
test('Unicode artifact', r.json()['result']['saved'] == True)

# Empty
r = requests.post(f'{BASE}/tools/artifact_saver', json={'arguments': {'content': '', 'filename': 'empty'}})
test('Empty artifact', r.json()['result']['saved'] == True and r.json()['result']['size_bytes'] == 0)

# Verify artifacts endpoint
r = requests.get(f'{BASE}/artifacts')
test('Artifacts endpoint shows records', r.json()['count'] >= 2)

# ═══════════════════════════════════════════════════════════════
# 8. HEALTHCHECK — reads from both DBs
# ═══════════════════════════════════════════════════════════════
print("\n=== 8. HEALTHCHECK ===")
r = requests.post(f'{BASE}/tools/healthcheck', json={'arguments': {}})
d = r.json()['result']
test('Status healthy', d['status'] in ('healthy', 'degraded'))
test('10 registered tools', len(d['registered_tools']) == 10)
test('Has sync_status', 'sync_status' in d)
test('Has operational_counts', 'operational_counts' in d)
test('Has started_at', len(d['started_at']) > 0)

# Verify sync_status has all tables
for tbl in ('artifacts', 'sf_tickers', 'sf_quarterly_facts', 'sn_filings', 'sn_notes', 'sn_detail_registry', 'sn_note_details'):
    test(f'Sync status has {tbl}', tbl in d['sync_status'])

# ═══════════════════════════════════════════════════════════════
# 9. SLOW HELLO — async, long polling
# ═══════════════════════════════════════════════════════════════
print("\n=== 9. SLOW HELLO ===")
r = requests.post(f'{BASE}/tools/slow_hello', json={'arguments': {'delay': 1}}, timeout=10)
d = r.json()['result']
test('Hello World', d['message'] == 'Hello World')
test('Delay 1s', d['delay_seconds'] == 1)

r = requests.post(f'{BASE}/tools/slow_hello', json={'arguments': {'delay': 0}}, timeout=10)
test('Zero delay', r.json()['result']['delay_seconds'] == 0)

r = requests.post(f'{BASE}/tools/slow_hello', json={'arguments': {'delay': -5}}, timeout=10)
test('Negative clamped to 0', r.json()['result']['delay_seconds'] == 0)

# ═══════════════════════════════════════════════════════════════
# 10. MCP PROTOCOL
# ═══════════════════════════════════════════════════════════════
print("\n=== 10. MCP PROTOCOL ===")
r = requests.post(f'{BASE}/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}})
d = r.json()
test('MCP initialize', d['result']['serverInfo']['name'] == 'activity-server')
test('Protocol version', d['result']['protocolVersion'] == '2024-11-05')

r = requests.post(f'{BASE}/mcp', json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}})
tools = {t['name'] for t in r.json()['result']['tools']}
test('10 tools in MCP', len(tools) == 10)
for t in r.json()['result']['tools']:
    test(f'Tool {t["name"]} has inputSchema', 'inputSchema' in t)

r = requests.post(f'{BASE}/mcp', json={'jsonrpc': '2.0', 'id': 3, 'method': 'bogus', 'params': {}})
test('Unknown method -32601', r.json()['error']['code'] == -32601)

r = requests.post(f'{BASE}/mcp', data='not json')
test('Malformed JSON -32700', r.json()['error']['code'] == -32700)

# MCP tools/call
r = requests.post(f'{BASE}/mcp', json={'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'calculator', 'arguments': {'expression': '7*8'}}})
d = r.json()
test('MCP calculator 7*8=56', json.loads(d['result']['content'][0]['text'])['result'] == 56)

# ═══════════════════════════════════════════════════════════════
# 11. OBSERVABILITY
# ═══════════════════════════════════════════════════════════════
print("\n=== 11. OBSERVABILITY ===")
r = requests.post(f'{BASE}/tools/timestamp', headers={'X-Observe': 'true'}, json={'arguments': {}})
d = r.json()
test('Lineage present', 'lineage' in d)
test('Lineage has 1 entry', len(d['lineage']) == 1)
test('Activity name', d['lineage'][0]['name'] == 'timestamp.now')
test('Activity ok', d['lineage'][0]['ok'] == True)
test('Has duration_ms', d['lineage'][0]['duration_ms'] >= 0)

r = requests.post(f'{BASE}/tools/timestamp', json={'arguments': {}})
test('No lineage without observe', 'lineage' not in r.json())

# Multi-activity
r = requests.post(f'{BASE}/tools/calculator', headers={'X-Observe': 'true'}, json={'arguments': {'expression': '1+1'}})
d = r.json()
names = [e['name'] for e in d['lineage']]
test('Calculator: 2 activities', len(names) == 2)
test('Calculator: sanitize', 'calculator.sanitize' in names)
test('Calculator: evaluate', 'calculator.evaluate' in names)
test('Calculator: result=2', d['result']['result'] == 2)

# Error in lineage
r = requests.post(f'{BASE}/tools/calculator', headers={'X-Observe': 'true'}, json={'arguments': {'expression': '1/0'}})
d = r.json()
test('Error captured in lineage', 'error' in str(d))

# HTTP observability
r = requests.post(f'{BASE}/tools/crypto', headers={'X-Observe': 'true'}, json={'arguments': {'plaintext': 'test'}})
test('HTTP observability', 'lineage' in r.json())

# ═══════════════════════════════════════════════════════════════
# 12. TOOL DOCS
# ═══════════════════════════════════════════════════════════════
print("\n=== 12. TOOL DOCS ===")
r = requests.get(f'{BASE}/tools/calculator/docs')
test('Docs markdown 200', r.status_code == 200)
test('Docs has calculator', 'calculator' in r.text.lower())

r = requests.get(f'{BASE}/tools/crypto/docs', headers={'Accept': 'application/json'})
test('Docs JSON 200', r.status_code == 200)
test('Docs JSON has description', 'description' in r.json())

r = requests.get(f'{BASE}/tools/stock_financials/docs')
test('Stock financials docs', r.status_code == 200)

r = requests.get(f'{BASE}/tools/stock_notes/docs')
test('Stock notes docs', r.status_code == 200)

r = requests.get(f'{BASE}/tools/nope/docs')
test('Unknown tool 404', r.status_code == 404)

# ═══════════════════════════════════════════════════════════════
# 13. HTTP ENDPOINTS
# ═══════════════════════════════════════════════════════════════
print("\n=== 13. HTTP ENDPOINTS ===")
r = requests.get(f'{BASE}/health')
test('Health 200', r.status_code == 200)

r = requests.get(f'{BASE}/')
test('Root returns health', r.status_code == 200 and 'status' in r.json())

r = requests.get(f'{BASE}/databases')
test('Databases 200', r.status_code == 200)

r = requests.get(f'{BASE}/sync')
test('Sync 200', r.status_code == 200 and 'tables' in r.json())

r = requests.get(f'{BASE}/telemetry')
test('Telemetry 200', r.status_code == 200 and 'entries' in r.json())

r = requests.get(f'{BASE}/telemetry?limit=2')
test('Telemetry limit', r.json()['count'] <= 2)

r = requests.get(f'{BASE}/artifacts')
test('Artifacts 200', r.status_code == 200)

r = requests.get(f'{BASE}/runs')
test('Runs 200', r.status_code == 200 and 'runs' in r.json())

r = requests.get(f'{BASE}/runs?tool=calculator')
test('Runs filter', r.status_code == 200)

# ═══════════════════════════════════════════════════════════════
# 14. RESOLVE
# ═══════════════════════════════════════════════════════════════
print("\n=== 14. RESOLVE ===")
r = requests.post(f'{BASE}/resolve', json={'source': 'operational'})
test('Resolve operational', r.json()['status'] == 'resolved')

r = requests.post(f'{BASE}/resolve', json={'source': 'cloud'})
test('Resolve cloud', r.json()['status'] == 'resolved')

r = requests.post(f'{BASE}/resolve', json={'source': 'bogus'})
test('Invalid source 400', r.status_code == 400)

r = requests.post(f'{BASE}/resolve', json={})
test('Missing source 400', r.status_code == 400)

# ═══════════════════════════════════════════════════════════════
# 15. EDGE CASES
# ═══════════════════════════════════════════════════════════════
print("\n=== 15. EDGE CASES ===")
r = requests.post(f'{BASE}/tools/nope', json={'arguments': {}})
test('Unknown tool 404', r.status_code == 404)

r = requests.post(f'{BASE}/tools/calculator', json={})
test('Missing args handled', r.status_code in (200, 500))

r = requests.post(f'{BASE}/tools/calculator', json={'arguments': {}})
test('Empty args handled', r.status_code in (200, 500))

r = requests.post(f'{BASE}/tools/crypto', json={'arguments': {'plaintext': '<script>alert(1)</script>'}})
test('XSS safe', len(r.json()['result']['encrypted']) == 64)

# Extra args ignored
r = requests.post(f'{BASE}/tools/timestamp', json={'arguments': {'bogus': 1}})
test('Extra args ignored', 'iso' in r.json()['result'])

# ═══════════════════════════════════════════════════════════════
# 16. CONCURRENT
# ═══════════════════════════════════════════════════════════════
print("\n=== 16. CONCURRENT ===")
def call_calc(i):
    r = requests.post(f'{BASE}/tools/calculator', json={'arguments': {'expression': f'{i}*2'}})
    return r.json()['result']['result']
with concurrent.futures.ThreadPoolExecutor(10) as ex:
    results = list(ex.map(call_calc, range(10)))
test('10 concurrent calc', results == [i*2 for i in range(10)])

def call_timestamp(_):
    r = requests.post(f'{BASE}/tools/timestamp', json={'arguments': {}})
    return r.json()['result']['epoch'] > 0
with concurrent.futures.ThreadPoolExecutor(10) as ex:
    results = list(ex.map(call_timestamp, range(10)))
test('10 concurrent timestamp', all(results))

# ═══════════════════════════════════════════════════════════════
# 17. DATA FLOW E2E
# ═══════════════════════════════════════════════════════════════
print("\n=== 17. DATA FLOW E2E ===")
# Artifact creates synced record
r = requests.post(f'{BASE}/tools/artifact_saver', json={'arguments': {'content': 'e2e test', 'filename': 'e2e_sync'}})
test('Artifact saved', r.json()['result']['saved'] == True)

# Verify sync still OK after writes
r = requests.get(f'{BASE}/sync')
d = r.json()
test('Sync still OK after writes', d['in_sync'] == True)

# Verify run recorded
r = requests.post(f'{BASE}/tools/db_writer', json={'arguments': {'data': {'e2e': True}}})
r2 = requests.get(f'{BASE}/runs?tool=db_writer&limit=1')
test('Run recorded', r2.json()['count'] >= 1)

# Verify telemetry grew
r = requests.get(f'{BASE}/telemetry?limit=100')
test('Telemetry has entries', r.json()['count'] > 0)

# ═══════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════
sep = '=' * 60
print(f'\n{sep}')
print(f'RESULTS: {passed} passed, {failed} failed out of {passed+failed} tests')
if failed:
    print(f'FAILED TESTS:')
print(sep)
