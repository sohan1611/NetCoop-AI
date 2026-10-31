"""Adversarial tests for final NetCoop AI audit, reviewed commit 4bb77b7.
These are ACCEPTANCE proposals, NOT verified failing tests or claims of deployment testing.
Copy to tests/test_final_qa.py and fixtures to tests/fixtures/final_qa/.
"""
import asyncio
import inspect
import json
from pathlib import Path

from fastapi.testclient import TestClient

from api import server
from netcoop.agents.normalizer import normalize
from netcoop.agents.connectivity import analyze_connectivity
from netcoop.agents.performance import analyze_performance
from netcoop.cli import build_orchestrator, _cause, _report

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'final_qa'


def raw(filename):
    return json.loads((FIXTURES / filename).read_text(encoding='utf-8'))


def run_report(data, tmp_path):
    state = asyncio.run(build_orchestrator(tmp_path, no_llm=True).run(data))
    report = _report(state)
    return _cause(state, report), report


def test_fqa01_untimed_disjoint_measurements_do_not_make_high_confidence_congestion(tmp_path):
    cause, report = run_report(raw('FQA01_no_timestamps_cross_record_correlation.json'), tmp_path)
    assert not (cause == 'congestion' and report.confidence >= 0.75), (
        f'No timestamps to establish simultaneity, yet {cause} confidence {report.confidence}. '
        'Require explicit uncertainty/quality cap before asserting a confident single root cause.'
    )


def test_fqa02_upstream_context_is_not_automatically_affected(tmp_path):
    cause, report = run_report(raw('FQA02_unaffected_upstream.json'), tmp_path)
    assert cause in {'latency_degradation', 'insufficient_evidence'}, cause
    assert 'CORE' not in report.affected_devices, (
        'CORE is healthy and only topologically upstream. Show as suspected upstream/context, '
        'not as measured affected device without independent evidence.'
    )


def test_fqa04_conflicting_device_status_has_order_independent_agent_results():
    one = normalize(raw('FQA04_conflicting_status_down_last.json'))
    two = normalize(raw('FQA04_conflicting_status_up_last.json'))
    assert any('conflicting status' in item for item in one.data_quality.issues)
    a = analyze_connectivity(one.telemetry)
    b = analyze_connectivity(two.telemetry)
    assert [(h.cause, h.score) for h in a.hypotheses] == [(h.cause, h.score) for h in b.hypotheses], (
        'Conflicting status order changed ConnectivityAgent scores. '
        'Neutralize ambiguous status before all specialist agents, not only the correlator.'
    )


def test_fqa07_evidence_identifies_mean_not_latest_when_only_mean_is_high():
    normalized = normalize(raw('FQA07_healthy_latest_high_historical_mean.json'))
    finding = analyze_performance(normalized.telemetry)
    misleading = [e for e in finding.evidence if e.metric == 'latency_ms'
                  and isinstance(e.value, (int, float)) and e.value < 100
                  and 'latest/mean is' in e.interpretation]
    assert not misleading, (
        'Latest value is below warning, but evidence text uses "latest/mean". '
        'State precisely that historic mean is high and the latest measurement recovered.'
    )


def test_fqa05_main_diagnosis_does_not_equate_consensus_with_confidence():
    script = (ROOT / 'api/static/app.js').read_text(encoding='utf-8')
    assert "'Multi-Agent Consensus: ' + percent(r?.confidence)" not in script, (
        'The diagnosis confidence is not the agent agreement fraction; render consensus k/n separately.'
    )


def test_fqa03_rate_limit_history_does_not_grow_from_rejected_forged_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'IP_RATE_LIMIT', 1)
    monkeypatch.setattr(server, 'RATE_LIMIT', 1000)
    app = server.create_app(tmp_path)
    endpoint = next(x.endpoint for x in app.routes if getattr(x, 'path', '') == '/api/upload')
    logs = inspect.getclosurevars(endpoint).nonlocals['upload_hit_log']
    with TestClient(app) as client:
        client.cookies.set('nc_session', 'first')
        assert client.post('/api/upload', content=b'{}').status_code == 200
        for i in range(30):
            client.cookies.set('nc_session', f'forged-session-{i}')
            assert client.post('/api/upload', content=b'{}').status_code == 429
    assert len(logs) <= 3, (
        f'Rate-limiter has {len(logs)} buckets after 30 rejected sessions; '
        'avoid recording unseen sessions on already-blocked IPs and evict inactive keys.'
    )
