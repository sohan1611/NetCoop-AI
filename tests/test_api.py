from fastapi.testclient import TestClient

from api.server import create_app


def _client(tmp_path):
    return TestClient(create_app(tmp_path))


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
