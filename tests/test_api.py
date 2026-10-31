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
    assert c.post('/api/run', json={}).status_code == 400
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
                                'max_samples': 50000, 'runs_per_min': 10}


def test_report_markdown(tmp_path):
    c = _client(tmp_path)
    run_id = _finished_run(c)
    res = c.get(f'/api/runs/{run_id}/report.md')
    assert res.status_code == 200 and res.headers['content-type'].startswith('text/markdown')
    assert res.text.startswith('# Incident') and 'CORE_RTR_01' in res.text
    assert c.get('/api/runs/nope/report.md').status_code == 404
