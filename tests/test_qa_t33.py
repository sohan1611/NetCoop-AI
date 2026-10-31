"""T33 regressions for rate admission, ambiguous status and evidence statistics."""

import asyncio
from collections import deque
import inspect
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from api import server
from netcoop.agents.normalizer import normalize
from netcoop.agents.performance import analyze_performance
from netcoop.agents.infrastructure import analyze_infrastructure
from netcoop.agents.connectivity import analyze_connectivity
from netcoop.cli import build_orchestrator, _report

ROOT = Path(__file__).resolve().parents[1]


def test_rejected_sessions_do_not_allocate_and_expired_buckets_are_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "IP_RATE_LIMIT", 1)
    monkeypatch.setattr(server, "RATE_LIMIT", 1)
    app = server.create_app(tmp_path)
    upload = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/api/upload")
    logs = inspect.getclosurevars(upload).nonlocals["upload_hit_log"]
    with TestClient(app) as client:
        client.cookies.set("nc_session", "first")
        assert client.post("/api/upload", content=b"{}").status_code == 200
        for index in range(30):
            client.cookies.set("nc_session", f"forged-{index}")
            assert client.post("/api/upload", content=b"{}").status_code == 429
        assert len(logs) == 2
        for hits in logs.values():
            hits.clear()
            hits.append(float("-inf"))
        logs.update({("session", f"expired-{i}"): deque([float("-inf")]) for i in range(1001)})
        assert client.post("/api/upload", content=b"{}").status_code == 200
        assert len(logs) == 2
        assert all(len(hits) == 1 for hits in logs.values())


def test_session_rejection_does_not_record_new_ip(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "RATE_LIMIT", 1)
    app = server.create_app(tmp_path)
    upload = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/api/upload")
    logs = inspect.getclosurevars(upload).nonlocals["upload_hit_log"]
    with TestClient(app) as client:
        assert client.post("/api/upload", content=b"{}").status_code == 200
        logs.pop(("ip", "testclient"))
        assert client.post("/api/upload", content=b"{}").status_code == 429
        assert ("ip", "testclient") not in logs


@pytest.mark.parametrize("section,identity", [
    ("devices", {"device_id": "A"}),
    ("services", {"server_id": "A", "name": "dns"}),
])
def test_status_conflicts_neutralize_every_row_and_preserve_numbers(section, identity):
    rows = [dict(identity, status=status, **({"cpu_pct": 20} if section == "devices"
                                          else {"success_rate_pct": 100}))
            for status in ("up", "down", "up")]
    findings = []
    for ordered in (rows, list(reversed(rows))):
        normalized = normalize({section: ordered})
        assert all(row["status"] is None for row in normalized.telemetry[section])
        metric = "cpu_pct" if section == "devices" else "success_rate_pct"
        assert [row[metric] for row in normalized.telemetry[section]] == [rows[0][metric]] * 3
        assert any("conflicting status" in issue for issue in normalized.data_quality.issues)
        findings.append([
            [(h.cause, h.score) for h in analyze(normalized.telemetry).hypotheses]
            for analyze in (analyze_performance, analyze_infrastructure, analyze_connectivity)
        ])
    assert findings[0] == findings[1]


def test_r3_conflict_stays_insufficient_without_device_down_rule(tmp_path):
    raw = json.loads((ROOT / "tests/fixtures/qa/R3-08_conflicting_device_status.json").read_text(encoding="utf-8"))
    state = asyncio.run(build_orchestrator(tmp_path, no_llm=True).run(raw))
    report = _report(state)
    assert report.status == "insufficient_evidence"
    assert "device reported down" not in report.model_dump_json()


@pytest.mark.parametrize("analyze,section,metric,high,low,threshold", [
    (analyze_performance, "samples", "latency_ms", 300, 10, 100),
    (analyze_infrastructure, "devices", "cpu_pct", 100, 70, 80),
])
@pytest.mark.parametrize("latest_high", [False, True])
def test_evidence_names_triggering_statistic(analyze, section, metric, high, low, threshold, latest_high):
    latest = high if latest_high else low
    finding = analyze({section: [
        {"device_id": "A", "timestamp": "2026-10-31T09:00:00Z", metric: high},
        {"device_id": "A", "timestamp": "2026-10-31T09:01:00Z", metric: latest},
        {"device_id": "A", "timestamp": "2026-10-31T09:02:00Z"},
    ]})
    evidence = next(e for e in finding.evidence if e.metric == metric)
    assert evidence.timestamp == "2026-10-31T09:01:00Z"
    if latest_high:
        assert evidence.value == latest
        assert evidence.interpretation.startswith(
            f"latest value is at or above the warning threshold {threshold}")
    else:
        assert evidence.value == round((high + low) / 2, 6)
        assert evidence.interpretation == (
            f"window mean over 2 samples is at or above the warning threshold {threshold}; "
            f"latest {low} at 2026-10-31T09:01:00Z is below it"
        )
    assert "latest/mean" not in evidence.interpretation


def test_rssi_evidence_preserves_inverted_threshold():
    finding = analyze_performance({"samples": [
        {"device_id": "A", "rssi_dbm": -90},
        {"device_id": "A", "rssi_dbm": -60},
    ]})
    evidence = next(e for e in finding.evidence if e.metric == "rssi_dbm")
    assert evidence.value == -75
    assert "window mean over 2 samples is at or below" in evidence.interpretation
    assert "latest -60 at None is above it" in evidence.interpretation


@pytest.mark.parametrize("severity", [None, "warning", "critical"])
def test_list_runs_exposes_report_severity_and_defaults(tmp_path, severity):
    with TestClient(server.create_app(tmp_path)) as client:
        client.get("/api/health")
        session = client.cookies.get("nc_session")
        directory = tmp_path / "severity-run"
        directory.mkdir()
        (directory / "owner.txt").write_text(session, encoding="utf-8")
        report = {"status": "diagnosed", "cause": "congestion", "confidence": 0.99}
        if severity is not None:
            report["severity"] = severity
        (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")
        assert client.get("/api/runs").json()[0]["severity"] == severity


def test_ui_confidence_agreement_and_severity_are_independent():
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for UI function regression")
    script = (ROOT / "api/static/app.js").read_text(encoding="utf-8")
    names = ("isFault", "renderSidebar", "renderHero", "renderRootCause")
    functions = "\n".join(
        f"function {name}(" + script.split(f"function {name}(", 1)[1].split("\n}", 1)[0] + "\n}"
        for name in names
    )
    harness = r"""
const assert = require('node:assert/strict');
const nodes = new Map();
function el(tag, text) {
  return {textContent: text, children: [], classList: {toggle() {}},
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; }};
}
function $(id) { if (!nodes.has(id)) nodes.set(id, el('div')); return nodes.get(id); }
const state = {messages: [], scenariosReady: true, acknowledged: new Set()};
const PIPELINE_AGENTS = [];
const inputRejected = () => false, relativeTime = () => '—', counted = () => '',
  incidentKey = r => r.run_id, sampleWindow = () => ({}),
  percent = v => Math.round(v * 100) + '%', label = v => v, humanText = v => v;
let votes = {k: 2, n: 3};
function votingConsensus() { return votes; }
"""
    checks = r"""
const report = {status: 'diagnosed', cause: 'congestion', confidence: 0.99,
  confidence_category: 'high', severity: 'warning'};
state.report = report;
renderHero();
assert.equal($('mesh-health').textContent, 'Campus Mesh: Degraded');
assert.equal($('incident-severity').textContent, 'P2');
report.severity = 'critical'; report.confidence = 0.6;
renderHero();
assert.equal($('mesh-health').textContent, 'Campus Mesh: Critical');
assert.equal($('incident-severity').textContent, 'P1 CRIT');
report.limitations = ['historical: recovered'];
renderHero();
assert.match($('mesh-health').textContent, /Recovered/);
assert.equal($('incident-severity').textContent, 'Resolved');
delete report.limitations; delete report.severity; report.confidence = 0.99;
renderHero();
assert.equal($('mesh-health').textContent, 'Campus Mesh: Unknown');
assert.equal($('incident-severity').textContent, 'Unclassified');
state.runs = [
  {...report, run_id: 'warning', severity: 'warning'},
  {...report, run_id: 'critical', severity: 'critical', confidence: 0.6},
  {...report, run_id: 'recovered', severity: 'critical', recovered: true},
  {...report, run_id: 'missing'},
];
renderSidebar();
assert.equal($('critical-badge').textContent, '1\nCRIT');
renderRootCause();
let meta = $('summary').children.at(-1);
assert.deepEqual(meta.children.map(child => child.textContent),
  ['Diagnosis confidence: 99% (high)', 'Agent agreement: 2/3']);
votes = null; renderRootCause();
meta = $('summary').children.at(-1);
assert.deepEqual(meta.children.map(child => child.textContent), ['Diagnosis confidence: 99% (high)']);
"""
    result = subprocess.run([node], input=harness + functions + checks, text=True,
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
