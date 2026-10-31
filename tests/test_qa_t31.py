"""T31 regressions for audit gates, aliases, quotas, persistence and recovery."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api import server
from netcoop import cli
from netcoop.agents.normalizer import normalize
from netcoop.bus import MessageBus
from netcoop.protocol import Envelope, MsgType
from netcoop.state import RunState
from tests.test_qa_t29 import render_js


@pytest.mark.parametrize("threshold,expected", [(1.0, 1), (0.75, 0)])
def test_evaluate_strict_default_and_explicit_smoke(tmp_path, monkeypatch, threshold, expected):
    (tmp_path / "labels.json").write_text(json.dumps(dict.fromkeys("abcd", "normal")))
    for name in "abcd":
        (tmp_path / f"{name}.json").write_text(name)
    class Runner:
        async def run(self, raw):
            return raw
    monkeypatch.setattr(cli, "build_orchestrator", lambda *a, **kw: Runner())
    monkeypatch.setattr(cli, "_report", lambda raw: SimpleNamespace(confidence=1))
    monkeypatch.setattr(cli, "_cause", lambda raw, report: "normal" if raw != "d" else "error")
    options = [] if threshold == 1 else ["--min-pass-rate", str(threshold)]
    assert cli.main(["evaluate", "--dir", str(tmp_path), "--no-llm", *options]) == expected


@pytest.mark.parametrize("canonical,alias,a,b", [
    ("collected_at", "collectedAt", "2026-10-31T10:00:00Z", "2026-10-31T09:00:00Z"),
    ("incident_id", "incidentId", "canonical", "alias"),
    ("schema_version", "version", "1.0", "99"),
    ("schema_version", "schemaVersion", "1.0", "0"),
])
def test_top_level_conflicts_canonical_first(canonical, alias, a, b):
    raw = {"schema_version": "1.0", canonical: a, alias: b, "devices": [], "samples": []}
    left, right = normalize(raw), normalize(dict(reversed(list(raw.items()))))
    assert left.telemetry == right.telemetry
    assert left.telemetry[canonical] == a
    issues = [x for x in left.data_quality.issues if "ambiguous top-level aliases" in x]
    assert issues == [f"ambiguous top-level aliases: {canonical} vs {alias} ({a} vs {b}); canonical value used"]


def test_equal_timestamp_instants_do_not_conflict():
    result = normalize({"collected_at": "2026-10-31T10:00:00Z",
                        "collectedAt": "2026-10-31T11:00:00+01:00"})
    assert not any("ambiguous top-level" in x for x in result.data_quality.issues)


@pytest.mark.parametrize("value", ["bad", "", None, 123])
def test_invalid_collection_reference_is_disclosed(value):
    result = normalize({"collected_at": value, "samples": [
        {"device_id": "A", "timestamp": "2026-10-31T10:00:00Z", "latency_ms": 10}]})
    assert f"invalid collected_at '{value}'; using newest sample time as reference" in result.data_quality.issues
    assert result.telemetry["collected_at"] == "2026-10-31T10:00:00Z"


def test_log_only_compacts_tasks(tmp_path):
    bus = MessageBus("compact", tmp_path)
    queue = bus.subscribe()
    telemetry = {"devices": [{"device_id": "A"}], "samples": [{"latency_ms": 10}]}
    task = Envelope(type=MsgType.TASK, sender="orchestrator", recipient="performance",
                    payload={"telemetry": telemetry})
    finding = Envelope(type=MsgType.FINDING, sender="normalizer", recipient="orchestrator",
                       payload={"telemetry": telemetry})
    async def emit():
        await bus._emit(task)
        await bus._emit(finding)
    asyncio.run(emit())
    logged = [json.loads(x) for x in (tmp_path / "compact/messages.jsonl").read_text().splitlines()]
    assert logged[0]["payload"]["telemetry"] == {"telemetry_ref": "normalizer", "devices": 1, "samples": 1}
    assert logged[1]["payload"]["telemetry"] == telemetry
    assert queue.get_nowait().payload["telemetry"] == telemetry
    assert task.payload["telemetry"] == telemetry


def test_checkpoint_stores_one_normalized_copy(tmp_path):
    telemetry = {"devices": [], "samples": [{"latency_ms": 10}]}
    state = RunState(run_id="one", input_ref="raw", telemetry=telemetry,
                     findings={"normalizer": {"telemetry": telemetry, "formats": ["canonical"]}})
    state.save(tmp_path)
    disk = json.loads((tmp_path / "one/state.json").read_text())
    assert disk["telemetry"] == telemetry
    assert "telemetry" not in disk["findings"]["normalizer"]
    assert RunState.load("one", tmp_path).telemetry == telemetry
    assert state.findings["normalizer"]["telemetry"] == telemetry


def test_sessions_share_ip_without_sharing_run_quota(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "build_orchestrator", lambda *a, **kw: SimpleNamespace(run=empty_run))
    with TestClient(server.create_app(tmp_path)) as client:
        for session in ("one", "two"):
            client.cookies.set("nc_session", session)
            for _ in range(10):
                response = client.post("/api/run", json={"scenario": "normal"})
                assert response.status_code == 200
                client.get(f"/api/runs/{response.json()['run_id']}/report")
            assert client.post("/api/run", json={"scenario": "normal"}).status_code == 429


async def empty_run(*a, **kw):
    return None


@pytest.mark.parametrize("route", ["run", "upload"])
def test_cookie_dropping_clients_still_hit_ip_cap(tmp_path, monkeypatch, route):
    monkeypatch.setattr(server, "build_orchestrator", lambda *a, **kw: SimpleNamespace(run=empty_run))
    with TestClient(server.create_app(tmp_path)) as client:
        for index in range(61):
            client.cookies.clear()
            options = {"json": {"scenario": "normal"}} if route == "run" else {"content": b"{}"}
            response = client.post(f"/api/{route}", headers={"X-Forwarded-For": str(index)}, **options)
            assert response.status_code == (200 if index < 60 else 429)
            if index < 60 and route == "run":
                client.get(f"/api/runs/{response.json()['run_id']}/report")
        assert response.headers["retry-after"] == "60"
        limits = client.get("/api/health").json()["limits"]
        assert limits["runs_per_min_per_session"] == limits["uploads_per_min_per_session"] == 10
        assert limits["runs_per_min_per_ip"] == limits["uploads_per_min_per_ip"] == 60


def test_list_cleanup_caps_runs_and_marks_recovery(tmp_path):
    for i in range(102):
        folder = tmp_path / f"run-{i:03}"
        folder.mkdir()
        (folder / "owner.txt").write_text("owner")
        (folder / "report.json").write_text(json.dumps({"status": "diagnosed", "confidence": .95,
            "cause": "congestion", "limitations": ["historical: recovered"] if i == 101 else []}))
        stamp = datetime.now().timestamp() - 200 + i
        os.utime(folder, (stamp, stamp))
    with TestClient(server.create_app(tmp_path)) as client:
        client.cookies.set("nc_session", "owner")
        rows = client.get("/api/runs").json()
    assert len(list(tmp_path.iterdir())) == 100
    assert not (tmp_path / "run-000").exists()
    assert not (tmp_path / "run-001").exists()
    assert rows[0]["recovered"] is True
    assert rows[1]["recovered"] is False


def test_large_run_storage_ratio_and_samples(tmp_path):
    start = datetime(2026, 10, 31, 10, tzinfo=timezone.utc)
    raw = {"schema_version": "1.0", "collected_at": start.isoformat(), "incident_id": "storage",
           "devices": [{"device_id": f"D{i}", "status": "up"} for i in range(10)],
           "samples": [{"device_id": f"D{i}", "timestamp": (start - timedelta(seconds=j)).isoformat(),
                        "latency_ms": 10, "jitter_ms": 1, "packet_loss_pct": 0, "throughput_mbps": 100}
                       for i in range(10) for j in range(1000)]}
    text = json.dumps(raw)
    state = asyncio.run(cli.build_orchestrator(tmp_path, no_llm=True).run(text))
    folder = tmp_path / state.run_id
    ratio = sum(p.stat().st_size for p in folder.iterdir()) / len(text.encode())
    print(f"run-dir size ratio: {ratio:.3f}x ({len(text.encode())} input bytes)")
    assert ratio <= 5
    messages = [json.loads(x) for x in (folder / "messages.jsonl").read_text().splitlines()]
    finding = next(m for m in messages if m["sender"] == "normalizer" and m["type"] == "FINDING")
    assert len(finding["payload"]["telemetry"]["samples"]) == 10000
    assert len(RunState.load(state.run_id, tmp_path).telemetry["samples"]) == 10000


def test_recovered_hero_and_critical_badge():
    setup = r"""
const assert = require('node:assert/strict');
const elements = {};
const $ = (id) => elements[id] ||= {classList: {toggle() {}}, textContent: ''};
const state = {report: {status: 'diagnosed', cause: 'congestion', confidence: .95,
 limitations: ['historical: latest samples healthy']}, messages: [], scenariosReady: true};
const inputRejected = () => false;
const isFault = (r) => r?.status === 'diagnosed' && r.cause !== 'normal';
const PIPELINE_AGENTS = [];
const relativeTime = () => '\u2014';
const counted = () => '\u2014';
"""
    render_js("renderHero", setup, r"""
assert.equal(elements['mesh-health'].textContent, 'Campus Mesh: Recovered \u2014 historical incident');
assert.equal(elements['mesh-health'].className, 'flagged');
assert.equal(elements['incident-severity'].textContent, 'Resolved');
""")
    render_js("renderSidebar", setup + """
state.runs = [{status: 'diagnosed', cause: 'congestion', confidence: .95, recovered: true}];
state.acknowledged = new Set();
const incidentKey = (r) => r.run_id;
""", "assert.equal(elements['critical-badge'].hidden, true);")


def test_upload_quota_is_per_session(tmp_path):
    with TestClient(server.create_app(tmp_path)) as client:
        for session in ("one", "two"):
            client.cookies.set("nc_session", session)
            for _ in range(10):
                assert client.post("/api/upload", content=b"").status_code == 400
            assert client.post("/api/upload", content=b"{}").status_code == 429


def test_run_cap_and_ttl_preserve_running_directory(tmp_path, monkeypatch):
    import threading
    entered, release = threading.Event(), threading.Event()
    async def blocked_run(*a, **kw):
        entered.set()
        release.wait(timeout=10)
    monkeypatch.setattr(server, "build_orchestrator", lambda *a, **kw: SimpleNamespace(run=blocked_run))
    monkeypatch.setattr(server, "MAX_STORED_RUNS", 1)
    with TestClient(server.create_app(tmp_path)) as client:
        try:
            run_id = client.post("/api/run", json={"scenario": "normal"}).json()["run_id"]
            assert entered.wait(timeout=5)
            folder = tmp_path / run_id
            os.utime(folder, (1, 1))
            for name in ("finished-one", "finished-two"):
                (tmp_path / name).mkdir()
            client.get("/api/runs")
            assert folder.is_dir()
            assert list(tmp_path.iterdir()) == [folder]
        finally:
            release.set()
        client.get(f"/api/runs/{run_id}/report")


def test_summary_accepts_full_and_reference_telemetry():
    source = (server.STATIC / "app.js").read_text(encoding="utf-8")
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js required")
    start = source.index("function summarize(")
    function = source[start:source.index("\n}", start) + 2]
    script = """
const assert = require('node:assert/strict');
const counted = (n, noun) => n + ' ' + noun;
const msg = {type: 'TASK', recipient: 'performance', payload: {}};
""" + function + """
for (const telemetry of [{devices: [{}], samples: [{}, {}]},
                         {telemetry_ref: 'normalizer', devices: 1, samples: 2}]) {
  assert.equal(summarize({...msg, payload: {telemetry}}), 'analyze 1 device / 2 sample');
}
"""
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
