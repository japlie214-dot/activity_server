#!/usr/bin/env python3
"""Quick integration tests against running server."""
import requests, json, time, concurrent.futures

BASE = 'http://127.0.0.1:8100'
passed = 0
failed = 0

def test(name, condition):
    global passed, failed
    if condition:
        print(f'  OK {name}')
        passed += 1
    else:
        print(f'  FAIL {name}')
        failed += 1

# 1. Health
print('=== 1. Health ===')
r = requests.get(f'{BASE}/health')
d = r.json()
test('Status 200', r.status_code == 200)
test('Healthy', d['status'] == 'healthy')
test('Tools unlocked', d['tools_locked'] == False)
test('10 tools', len(d['tools']) == 10)

# 2. Databases & Sync
print('\n=== 2. Databases & Sync ===')
r = requests.get(f'{BASE}/databases')
d = r.json()
test('Operational OK', 'operational' in d)
test('Cloud OK', 'cloud' in d)
test('In sync', d['in_sync'] == True)
test('SF tables exist', d['cloud']['tables'].get('sf_quarterly_facts', {}).get('exists', False))

# 3. MCP
print('\n=== 3. MCP ===')
r = requests.post(f'{BASE}/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}})
test('Init', r.json()['result']['serverInfo']['name'] == 'activity-server')
r = requests.post(f'{BASE}/mcp', json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}})
tools = {t['name'] for t in r.json()['result']['tools']}
test('10 tools in MCP', len(tools) == 10)

# 4. Calculator
print('\n=== 4. Calculator ===')
test('2+3=5', requests.post(f'{BASE}/tools/calculator', json={'arguments': {'expression': '2+3'}}).json()['result']['result'] == 5)
test('sqrt', requests.post(f'{BASE}/tools/calculator', json={'arguments': {'expression': '2+3*sqrt(16)'}}).json()['result']['result'] == 14.0)
test('sin(0)', requests.post(f'{BASE}/tools/calculator', json={'arguments': {'expression': 'sin(0)'}}).json()['result']['result'] == 0.0)

# 5. Crypto
print('\n=== 5. Crypto ===')
r = requests.post(f'{BASE}/tools/crypto', json={'arguments': {'plaintext': 'hello'}})
test('SHA256', len(r.json()['result']['encrypted']) == 64)

# 6. Timestamp
print('\n=== 6. Timestamp ===')
r = requests.post(f'{BASE}/tools/timestamp', json={'arguments': {}})
test('ISO', 'T' in r.json()['result']['iso'])
test('Epoch', r.json()['result']['epoch'] > 0)

# 7. TextStats
print('\n=== 7. TextStats ===')
r = requests.post(f'{BASE}/tools/textstats', json={'arguments': {'text': 'The quick brown fox.'}})
test('Words=4', r.json()['result']['word_count'] == 4)

# 8. DB Writer
print('\n=== 8. DB Writer ===')
r = requests.post(f'{BASE}/tools/db_writer', json={'arguments': {'data': {'k': 'v'}, 'label': 't'}})
test('Saved', r.json()['result']['saved'] == True)

# 9. Artifact Saver
print('\n=== 9. Artifact Saver ===')
r = requests.post(f'{BASE}/tools/artifact_saver', json={'arguments': {'content': 'test', 'filename': 't1'}})
test('.txt', r.json()['result']['filename'] == 't1.txt')

# 10. HealthCheck
print('\n=== 10. HealthCheck ===')
r = requests.post(f'{BASE}/tools/healthcheck', json={'arguments': {}})
test('OK', r.json()['result']['status'] in ('healthy', 'degraded'))

# 11. Observability
print('\n=== 11. Observability ===')
r = requests.post(f'{BASE}/tools/timestamp', headers={'X-Observe': 'true'}, json={'arguments': {}})
test('Lineage on', 'lineage' in r.json())
r = requests.post(f'{BASE}/tools/timestamp', json={'arguments': {}})
test('Lineage off', 'lineage' not in r.json())

# 12. Multi-Activity
print('\n=== 12. Multi-Activity ===')
r = requests.post(f'{BASE}/tools/calculator', headers={'X-Observe': 'true'}, json={'arguments': {'expression': '1+1'}})
names = [e['name'] for e in r.json()['lineage']]
test('2 activities', len(names) == 2)

# 13. Docs
print('\n=== 13. Docs ===')
test('MD', requests.get(f'{BASE}/tools/calculator/docs').status_code == 200)
test('JSON', 'description' in requests.get(f'{BASE}/tools/crypto/docs', headers={'Accept': 'application/json'}).json())

# 14. Endpoints
print('\n=== 14. Endpoints ===')
test('Telemetry', 'count' in requests.get(f'{BASE}/telemetry').json())
test('Artifacts', 'artifacts' in requests.get(f'{BASE}/artifacts').json())
test('Runs', 'runs' in requests.get(f'{BASE}/runs').json())

# 15. Resolve
print('\n=== 15. Resolve ===')
test('Op resolve', requests.post(f'{BASE}/resolve', json={'source': 'operational'}).json()['status'] == 'resolved')
test('Cloud resolve', requests.post(f'{BASE}/resolve', json={'source': 'cloud'}).json()['status'] == 'resolved')
test('Invalid 400', requests.post(f'{BASE}/resolve', json={'source': 'bogus'}).status_code == 400)

# 16. Edge Cases
print('\n=== 16. Edge Cases ===')
test('Unknown 404', requests.post(f'{BASE}/tools/nope', json={'arguments': {}}).status_code == 404)
test('XSS safe', len(requests.post(f'{BASE}/tools/crypto', json={'arguments': {'plaintext': '<script>'}}).json()['result']['encrypted']) == 64)

# 17. Concurrent
print('\n=== 17. Concurrent ===')
def call(i):
    return requests.post(f'{BASE}/tools/calculator', json={'arguments': {'expression': f'{i}*2'}}).json()['result']['result']
with concurrent.futures.ThreadPoolExecutor(10) as ex:
    results = list(ex.map(call, range(10)))
test('10 concurrent', results == [i*2 for i in range(10)])

# Summary
sep = '=' * 60
print(f'\n{sep}')
print(f'RESULTS: {passed} passed, {failed} failed out of {passed+failed} tests')
print(sep)
