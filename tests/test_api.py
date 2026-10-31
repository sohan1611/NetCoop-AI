import asyncio
from fastapi.testclient import TestClient

from api.server import create_app


def _client(tmp_path):
    # Entered (never exited) so the portal/event loop outlives each request;
    # runs execute in worker threads and would be cancelled otherwise.
    return TestClient(create_app(tmp_path)).__enter__()


def test_list_scenarios(tmp_path):
    names = {s['name'] for s in _client(tmp_path).get('/api/scenarios').json()}
    assert {'congestion', 'dns_failure'} <= names


def test_run_congestion_report_stream_topology_remediate(tmp_path):
    c = _client(tmp_path)
    run_id = c.post('/api/run', json={'scenario': 'congestion'}).json()['run_id']
    with c.stream('GET', f'/api/runs/{run_id}/stream') as r:
        body = ''.join(r.iter_text())
    assert 'event: done' in body and '"sender"' in body
    report = c.get(f'/api/runs/{run_id}/report').json()
    assert report['cause'] == 'congestion'
    topo = c.get(f'/api/runs/{run_id}/topology').json()
    assert topo['nodes'] and set(topo['affected']) <= {n['id'] for n in topo['nodes']}
    fix = c.post(f'/api/runs/{run_id}/remediate').json()
    assert fix['before'] == 'congestion' and fix['verified'] is True


def test_malformed_upload_is_not_500(tmp_path):
    c = _client(tmp_path)
    up = c.post('/api/upload', content=b'{not json').json()['upload_id']
    run_id = c.post('/api/run', json={'upload_id': up}).json()['run_id']
    res = c.get(f'/api/runs/{run_id}/report')
    assert res.status_code == 200 and res.json()['status'] == 'error'
    assert c.post(f'/api/runs/{run_id}/remediate').status_code == 409


def test_bad_inputs(tmp_path):
    c = _client(tmp_path)
    assert c.post('/api/upload', content=b'').status_code == 400
    assert c.post('/api/run', json={}).status_code == 422
    assert c.post('/api/run', json={'scenario': '../x'}).status_code == 400
    assert c.get('/api/runs/nope/report').status_code == 404


# --- T17 V1-V3 ---------------------------------------------------------------
import json
import os
import time

import api.server as server


class _SlowOrchestrator:
    async def run(self, raw, run_id=None):
        await asyncio.sleep(0.8)


def _slow(monkeypatch):
    monkeypatch.setattr(server, 'build_orchestrator', lambda *a, **k: _SlowOrchestrator())


def test_third_concurrent_run_gets_429(tmp_path, monkeypatch):
    _slow(monkeypatch)
    with TestClient(create_app(tmp_path)) as c:
        codes = [c.post('/api/run', json={'scenario': 'congestion'}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_per_ip_rate_limit(tmp_path, monkeypatch):
    class Fast:
        async def run(self, raw, run_id=None):
            return None

    monkeypatch.setattr(server, 'build_orchestrator', lambda *a, **k: Fast())
    monkeypatch.setattr(server, 'RATE_LIMIT', 3)
    monkeypatch.setattr(server, 'MAX_CONCURRENT_RUNS', 50)
    with TestClient(create_app(tmp_path)) as c:
        codes = [c.post('/api/run', json={'scenario': 'congestion'}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_event_loop_stays_responsive_during_run(tmp_path, monkeypatch):
    class Blocking:
        async def run(self, raw, run_id=None):
            time.sleep(1.0)  # sync CPU work, as the agents do

    monkeypatch.setattr(server, 'build_orchestrator', lambda *a, **k: Blocking())
    with TestClient(create_app(tmp_path)) as c:
        c.post('/api/run', json={'scenario': 'congestion'})
        start = time.monotonic()
        assert c.get('/api/scenarios').status_code == 200
        assert time.monotonic() - start < 0.5


def test_oversize_input_is_error_report(tmp_path):
    c = _client(tmp_path)
    rows = ','.join('{"device_id":"D%d"}' % i for i in range(25_001))
    up = c.post('/api/upload', content=('[' + rows + ']').encode()).json()['upload_id']
    run_id = c.post('/api/run', json={'upload_id': up}).json()['run_id']
    res = c.get(f'/api/runs/{run_id}/report').json()
    assert res['status'] == 'error'
    assert 'input too large' in json.dumps(res)


def test_upload_over_5mb_is_413(tmp_path):
    c = _client(tmp_path)
    assert c.post('/api/upload', content=b'x' * 5_000_001).status_code == 413


def test_old_runs_and_uploads_are_cleaned(tmp_path, monkeypatch):
    class Fast:
        async def run(self, raw, run_id=None):
            return None

    monkeypatch.setattr(server, 'build_orchestrator', lambda *a, **k: Fast())
    monkeypatch.setenv('NETCOOP_RUN_TTL_S', '60')
    old_run = tmp_path / 'oldrun'
    old_run.mkdir()
    (tmp_path / 'uploads').mkdir()
    old_up = tmp_path / 'uploads' / 'old.json'
    old_up.write_text('{}')
    fresh_up = tmp_path / 'uploads' / 'fresh.json'
    fresh_up.write_text('{}')
    stale = time.time() - 600
    os.utime(old_run, (stale, stale))
    os.utime(old_up, (stale, stale))
    with TestClient(create_app(tmp_path)) as c:
        assert c.post('/api/run', json={'scenario': 'congestion'}).status_code == 200
    assert not old_run.exists() and not old_up.exists()
    assert fresh_up.exists() and (tmp_path / 'uploads').is_dir()


# --- T17 V5-V7 ---------------------------------------------------------------
def test_security_headers_and_sri(tmp_path):
    c = _client(tmp_path)
    res = c.get('/api/scenarios')
    assert res.headers['x-content-type-options'] == 'nosniff'
    assert res.headers['x-frame-options'] == 'DENY'
    assert res.headers['referrer-policy'] == 'no-referrer'
    assert "script-src 'self' https://cdnjs.cloudflare.com" in res.headers['content-security-policy']
    page = c.get('/').text
    assert 'integrity="sha512-' in page and 'crossorigin=' in page


def test_labels_is_not_a_scenario(tmp_path):
    c = _client(tmp_path)
    assert c.get('/api/scenarios/labels').status_code == 400
    assert c.post('/api/run', json={'scenario': 'labels'}).status_code == 400


def test_api_errors_do_not_leak_exception_text(tmp_path):
    c = _client(tmp_path)
    cases = {'deep': '[' * 100000 + ']' * 100000,
             'bigint': '{"device_id":"X","metrics":{"latency_ms":' + '9' * 5000 + '}}'}
    for body in cases.values():
        up = c.post('/api/upload', content=body.encode()).json()['upload_id']
        run_id = c.post('/api/run', json={'upload_id': up}).json()['run_id']
        text = json.dumps(c.get(f'/api/runs/{run_id}/report').json())
        for leak in ('set_int_max_str_digits', 'Stack overflow', 'RecursionError', 'ValueError', 'Traceback'):
            assert leak not in text
    run_id = c.post('/api/run', json={'upload_id': up}).json()['run_id']
    c.get(f'/api/runs/{run_id}/report')
    detail = c.post(f'/api/runs/{run_id}/remediate').json()['detail']
    assert detail.startswith('cannot remediate') and 'Error' not in detail


# --- T18 A1-A6 ---------------------------------------------------------------
def _finished_run(c, scenario='congestion'):
    run_id = c.post('/api/run', json={'scenario': scenario}).json()['run_id']
    c.get(f'/api/runs/{run_id}/report')  # waits for the run
    return run_id


def test_details_shape_for_congestion(tmp_path):
    c = _client(tmp_path)
    d = c.get(f'/api/runs/{_finished_run(c)}/details').json()
    assert d['incident_id'] and d['status'] == 'diagnosed' and d['rounds'] >= 0
    assert d['started_at'] <= d['finished_at']
    assert set(d['window']) == {'first', 'last', 'seconds'}
    assert d['formats'] == ['canonical v1']
    assert [a['name'] for a in d['agents']] == [
        'normalizer', 'performance', 'infrastructure', 'connectivity', 'correlator']
    allowed = {'complete', 'flagged', 'alert', 'failed', 'abstained'}
    assert all(a['status'] in allowed and 'stats' in a and 'summary' in a for a in d['agents'])
    assert set(d['consensus']) == {'engaged', 'total', 'in_accord', 'synchronized'}
    assert d['consensus']['total'] == 5 and d['consensus']['in_accord'] <= d['consensus']['engaged']
    worst = next(x for x in d['devices'] if x['device_id'] == 'CORE_RTR_01')
    assert worst['severity'] == 'critical' and worst['role'] and worst['worst_metric']
    assert d['latency_series'] and set(d['latency_series'][0]) == {'t', 'value'}
    assert 'CORE_RTR_01' in d['primary_rationale']
    assert len(d['mappings']) <= 50 and d['mappings'][0]['device_id'] == 'CORE_RTR_01'
    assert set(d['mappings'][0]) == {'source_format', 'device_id', 'raw_path', 'raw_value',
                                     'metric', 'value', 'unit', 'note'}


def test_details_records_organizer_format_and_failed_agent(tmp_path):
    c = _client(tmp_path)
    raw = (server.ROOT / 'data' / 'scenarios' / 'org_hardware_failure.json').read_bytes()
    up = c.post('/api/upload', content=raw).json()['upload_id']
    run_id = c.post('/api/run', json={'upload_id': up, 'fail_agent': 'performance'}).json()['run_id']
    c.get(f'/api/runs/{run_id}/report')
    d = c.get(f'/api/runs/{run_id}/details').json()
    assert d['formats'][0] == 'organizer' and 'aliased fields' in d['formats']
    assert any(m['raw_path'].startswith('metrics.') for m in d['mappings'])
    performance = next(a for a in d['agents'] if a['name'] == 'performance')
    assert performance['status'] == 'failed'
    connectivity = next(a for a in d['agents'] if a['name'] == 'connectivity')
    assert connectivity['status'] == 'abstained'


def test_details_for_error_run_is_partial_not_500(tmp_path):
    c = _client(tmp_path)
    up = c.post('/api/upload', content=b'{not json').json()['upload_id']
    run_id = c.post('/api/run', json={'upload_id': up}).json()['run_id']
    c.get(f'/api/runs/{run_id}/report')
    res = c.get(f'/api/runs/{run_id}/details')
    assert res.status_code == 200
    d = res.json()
    assert d['status'] == 'error' and d['devices'] == [] and d['mappings'] == []
    assert c.get('/api/runs/nope/details').status_code == 404


def test_runs_list_is_recent_first_and_capped(tmp_path):
    c = _client(tmp_path)
    ids = [_finished_run(c) for _ in range(3)]
    rows = c.get('/api/runs').json()
    assert [r['run_id'] for r in rows[:3]] == ids[::-1]
    assert set(rows[0]) == {'run_id', 'incident_id', 'cause', 'status', 'confidence', 'created_at'}
    assert rows[0]['cause'] == 'congestion' and rows[0]['status'] == 'diagnosed'
    assert len(rows) <= 20


def test_playbook_and_health(tmp_path):
    c = _client(tmp_path)
    book = c.get('/api/playbook').json()
    assert {'congestion', 'hardware_failure', 'insufficient_evidence'} <= {b['cause'] for b in book}
    assert all(b['actions'] and 'remediation' in b for b in book)
    health = c.get('/api/health').json()
    assert health['version'] and health['uptime_s'] >= 0 and health['llm_enabled'] is False
    assert health['limits'] == {'max_upload_mb': 5, 'max_devices': 25000,
                                'max_samples': 50000, 'max_nodes': 200, 'max_links': 1000,
                                'max_services': 200, 'runs_per_min': 10}


def test_report_markdown(tmp_path):
    c = _client(tmp_path)
    run_id = _finished_run(c)
    res = c.get(f'/api/runs/{run_id}/report.md')
    assert res.status_code == 200 and res.headers['content-type'].startswith('text/markdown')
    assert res.text.startswith('# Incident') and 'CORE_RTR_01' in res.text
    assert c.get('/api/runs/nope/report.md').status_code == 404


# --- T25: QA API/backend -----------------------------------------------------
def test_nc007_two_session_isolation_and_persisted_ownership(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as a, TestClient(app) as b:
        run_id = _finished_run(a)
        cookie = a.cookies.get('nc_session')
        assert len(cookie) == 32
        assert (tmp_path / run_id / 'owner.txt').read_text() == cookie
        upload_id = a.post('/api/upload', content=b'{not json').json()['upload_id']
        assert (tmp_path / 'uploads' / f'{upload_id}.owner').read_text() == cookie
        assert run_id not in {row['run_id'] for row in b.get('/api/runs').json()}
        for endpoint in ('stream', 'report', 'report.md', 'details', 'topology'):
            url = f'/api/runs/{run_id}/{endpoint}'
            denied = b.get(url)
            assert denied.status_code == 404 and denied.json()['detail'] == 'unknown run'
            assert a.get(url).status_code == 200
        assert b.post(f'/api/runs/{run_id}/remediate').status_code == 404
        denied = b.post('/api/run', json={'upload_id': upload_id})
        assert denied.status_code == 404 and denied.json()['detail'] == 'input not found'
        own_upload = a.post('/api/run', json={'upload_id': upload_id})
        assert own_upload.status_code == 200
        a.get(f"/api/runs/{own_upload.json()['run_id']}/report")
        fix = a.post(f'/api/runs/{run_id}/remediate')
        assert fix.status_code == 200
        new_id = fix.json()['run_id']
        assert (tmp_path / new_id / 'owner.txt').read_text() == cookie
        assert b.get(f'/api/runs/{new_id}/report').status_code == 404
        assert a.get('/api/scenarios/congestion').status_code == 200
        assert b.get('/api/scenarios/congestion').status_code == 200
    with TestClient(create_app(tmp_path)) as restarted:
        restarted.cookies.set('nc_session', cookie)
        assert restarted.get(f'/api/runs/{run_id}/report').status_code == 200
        assert run_id in {row['run_id'] for row in restarted.get('/api/runs').json()}
        assert restarted.post('/api/run', json={'upload_id': upload_id}).status_code == 200


def test_nc007_cookie_and_stream_redaction(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        response = c.get('/api/scenarios')
        cookie = response.headers['set-cookie'].lower()
        assert 'httponly' in cookie and 'samesite=lax' in cookie and 'path=/' in cookie
        raw = '{not json: private telemetry é'
        upload_id = c.post('/api/upload', content=raw.encode()).json()['upload_id']
        run_id = c.post('/api/run', json={'upload_id': upload_id}).json()['run_id']
        body = c.get(f'/api/runs/{run_id}/stream').text
        messages = [json.loads(line[6:]) for line in body.splitlines()
                    if line.startswith('data: ') and line != 'data: {}']
        task = next(m for m in messages if 'raw' in m.get('payload', {}))
        assert task['payload']['raw'] == f'[raw input redacted: {len(raw.encode())} bytes]'
        assert 'private telemetry' not in body
        persisted = [json.loads(line) for line in
                     (tmp_path / run_id / 'messages.jsonl').read_text(encoding='utf-8').splitlines()]
        assert next(m['payload']['raw'] for m in persisted if 'raw' in m.get('payload', {})) == raw
        legacy = tmp_path / 'ownerless'
        legacy.mkdir()
        for endpoint in ('stream', 'report', 'report.md', 'details', 'topology'):
            assert c.get(f'/api/runs/ownerless/{endpoint}').status_code == 404
        assert c.post('/api/runs/ownerless/remediate').status_code == 404


def test_nc008_upload_rate_limit_separate_from_runs_and_no_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'RATE_LIMIT', 2)
    monkeypatch.setattr(server, 'MAX_CONCURRENT_RUNS', 0)
    with TestClient(create_app(tmp_path)) as c:
        assert c.post('/api/upload', content=b'{bad').status_code == 200
        assert c.post('/api/upload', content=b'{}').status_code == 200
        assert c.post('/api/upload', content=b'{}').status_code == 429
        monkeypatch.setattr(server, 'MAX_CONCURRENT_RUNS', 2)
        run_id = c.post('/api/run', json={'scenario': 'congestion'}).json()['run_id']
        assert c.get(f'/api/runs/{run_id}/report').status_code == 200
        monkeypatch.setattr(server, 'RATE_WINDOW_S', -1)
        assert c.post('/api/upload', content=b'{}').status_code == 200


def test_nc008_upload_total_count_bytes_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'MAX_UPLOAD_FILES', 2)
    monkeypatch.setattr(server, 'MAX_UPLOAD_TOTAL_BYTES', 6)
    with TestClient(create_app(tmp_path)) as c:
        first = c.post('/api/upload', content=b'1234').json()['upload_id']
        assert c.post('/api/upload', content=b'123').status_code == 413
        second = c.post('/api/upload', content=b'12').json()['upload_id']
        monkeypatch.setattr(server, 'MAX_UPLOAD_TOTAL_BYTES', 100)
        assert c.post('/api/upload', content=b'1').status_code == 413
        old = tmp_path / 'old'
        old.mkdir()
        stale = time.time() - 600
        os.utime(old, (stale, stale))
        monkeypatch.setenv('NETCOOP_RUN_TTL_S', '60')
        for suffix in ('json', 'owner'):
            os.utime(tmp_path / 'uploads' / f'{first}.{suffix}', (stale, stale))
        assert c.post('/api/upload', content=b'{bad').status_code == 200
        assert not old.exists()
        assert not (tmp_path / 'uploads' / f'{first}.json').exists()
        assert not (tmp_path / 'uploads' / f'{first}.owner').exists()
        assert (tmp_path / 'uploads' / f'{second}.json').exists()


def test_nc015_dangling_topology_fixture(tmp_path):
    from netcoop.agents.connectivity import _build_graph
    from netcoop.agents.normalizer import normalize

    raw = (server.ROOT / 'tests/fixtures/qa/09_topology_dangling_link.json').read_text()
    original = json.loads(raw)
    graph, _, notes = _build_graph(original['topology'])
    assert set(graph) == {'CORE'} and not graph.edges
    assert any('CORE -> NONEXISTENT-SW' in note for note in notes)
    result = normalize(raw)
    assert result.telemetry['topology']['links'] == []
    assert any('CORE -> NONEXISTENT-SW' in issue for issue in result.data_quality.issues)
    with TestClient(create_app(tmp_path)) as c:
        upload_id = c.post('/api/upload', content=raw.encode()).json()['upload_id']
        run_id = c.post('/api/run', json={'upload_id': upload_id}).json()['run_id']
        topo = c.get(f'/api/runs/{run_id}/topology').json()
        assert {node['id'] for node in topo['nodes']} == {'CORE'} and topo['links'] == []
        report = c.get(f'/api/runs/{run_id}/report').json()
        assert 'NONEXISTENT-SW' in json.dumps(report['limitations'])


def test_nc020_unknown_stream_is_404(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        response = c.get('/api/runs/unknown/stream')
        assert response.status_code == 404
        assert response.json()['detail'] == 'unknown run'


def test_nc021_topology_service_caps_and_health(tmp_path):
    from netcoop.agents.normalizer import normalize, MAX_NODES, MAX_LINKS, MAX_SERVICES

    raw = json.loads((server.SCENARIOS / 'congestion.json').read_text())
    raw['topology'] = {
        'nodes': [{'id': f'N{i}'} for i in range(MAX_NODES + 1)],
        'links': [{'src': 'N0', 'dst': 'N1'} for _ in range(MAX_LINKS + 1)],
    }
    raw['services'] = [{'name': 'dns', 'server_id': f'N{i}'} for i in range(MAX_SERVICES + 1)]
    result = normalize(raw)
    assert len(result.telemetry['topology']['nodes']) == MAX_NODES
    assert len(result.telemetry['topology']['links']) == MAX_LINKS
    assert len(result.telemetry['services']) == MAX_SERVICES
    for section in ('topology.nodes', 'topology.links', 'services'):
        assert any(section + ' truncated' in issue for issue in result.data_quality.issues)
    with TestClient(create_app(tmp_path)) as c:
        limits = c.get('/api/health').json()['limits']
        assert (limits['max_nodes'], limits['max_links'], limits['max_services']) == (200, 1000, 200)
        upload_id = c.post('/api/upload', content=json.dumps(raw).encode()).json()['upload_id']
        run_id = c.post('/api/run', json={'upload_id': upload_id}).json()['run_id']
        report = c.get(f'/api/runs/{run_id}/report').json()
        for section in ('topology.nodes', 'topology.links', 'services'):
            assert section + ' truncated' in json.dumps(report['limitations'])


def test_nc032_exactly_one_input(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        for body in ({}, {'scenario': None, 'upload_id': None},
                     {'scenario': 'congestion', 'upload_id': 'anything'}):
            response = c.post('/api/run', json=body)
            assert response.status_code == 422
            assert response.json()['detail'] == 'provide exactly one of scenario or upload_id'
        assert c.post('/api/run', json={'scenario': 'congestion'}).status_code == 200


def test_nc033_fatal_and_message_only_runs_listed_as_error(tmp_path, monkeypatch):
    import threading

    entered, unblock = threading.Event(), threading.Event()

    class Fatal:
        async def run(self, raw, run_id=None):
            entered.set()
            unblock.wait(5)
            raise RuntimeError('test failure')

    monkeypatch.setattr(server, 'build_orchestrator', lambda *a, **k: Fatal())
    with TestClient(create_app(tmp_path)) as c:
        run_id = c.post('/api/run', json={'scenario': 'congestion'}).json()['run_id']
        try:
            assert entered.wait(2)
            row = next(row for row in c.get('/api/runs').json() if row['run_id'] == run_id)
            assert row['status'] == 'running' and row['cause'] is None
        finally:
            unblock.set()
        c.get(f'/api/runs/{run_id}/stream')  # waits for fatal.txt
        assert (tmp_path / run_id / 'fatal.txt').exists()
        directory = tmp_path / 'messagesonly'
        directory.mkdir()
        (directory / 'owner.txt').write_text(c.cookies.get('nc_session'))
        (directory / 'messages.jsonl').write_text('')
        rows = {row['run_id']: row for row in c.get('/api/runs').json()}
        for failed in (run_id, 'messagesonly'):
            assert rows[failed]['status'] == rows[failed]['cause'] == 'error'
