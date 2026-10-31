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
