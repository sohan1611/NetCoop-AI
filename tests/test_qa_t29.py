"""T29 regressions: bounded locality, quarantine, UI, and run admission."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
from time import perf_counter

import pytest
from fastapi.testclient import TestClient

from api import server
from netcoop.agents.correlator import _local_scopes
from netcoop.agents.normalizer import normalize
from netcoop.cli import build_orchestrator
from netcoop.config import SIGNATURES

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/qa"


def fixture(prefix):
    return json.loads(next(FIXTURES.glob(f"{prefix}_*.json")).read_text(encoding="utf-8"))


def run_report(raw, tmp_path):
    state = asyncio.run(build_orchestrator(tmp_path, no_llm=True).run(raw))
    return json.loads((tmp_path / state.run_id / "report.json").read_text(encoding="utf-8"))


def test_rqa03_ten_minute_bridge_does_not_corroborate(tmp_path):
    report = run_report(fixture("14"), tmp_path)
    assert report["confidence"] <= 0.5


def test_rqa01_rqa03_bucket_families_and_supported_scopes():
    reference = datetime(2026, 10, 31, 9, tzinfo=timezone.utc)
    rows = [dict(device_id="A", timestamp=(reference + timedelta(seconds=s)).isoformat(),
                 latency_ms=250 if s < 600 else 10) for s in (149, 150, 299, 300, 600)]
    telemetry = dict(collected_at=reference.isoformat(), devices=[dict(device_id="A", status="up")],
                     services=[], samples=rows + [dict(device_id="A", jitter_ms=55)],
                     topology={"nodes": [], "links": []})
    scopes = list(_local_scopes(telemetry, {"A"}, SIGNATURES["latency_degradation"]))
    assert any(rows[0] in s["samples"] and rows[2] in s["samples"] for s in scopes)
    assert any(rows[1] in s["samples"] and rows[3] in s["samples"] for s in scopes)
    assert not any(rows[4] in s["samples"] for s in scopes)  # Healthy-only buckets skipped.
    assert any(s["samples"] == [telemetry["samples"][-1]] and not s["devices"] for s in scopes)
    for scope in scopes:
        timed = [datetime.fromisoformat(row["timestamp"]).timestamp()
                 for row in scope["samples"] if "timestamp" in row]
        if timed:
            assert max(timed) - min(timed) <= 300
            assert all("timestamp" in row for row in scope["samples"])
        if scope["devices"]:
            assert all(abs(t - reference.timestamp()) <= 300 for t in timed)


@pytest.mark.performance
@pytest.mark.parametrize("devices,samples,bound", [(1, 4000, 2), (5, 3600, 10)])
def test_rqa01_dense_sample_performance(devices, samples, bound, tmp_path):
    reference = datetime(2026, 10, 31, 10, tzinfo=timezone.utc)
    raw = fixture("00")
    raw["collected_at"] = reference.isoformat()
    raw["topology"] = {"nodes": [], "links": []}
    raw["services"] = []
    raw["devices"] = [dict(device_id=f"D{i}", status="up", cpu_pct=20, mem_pct=20,
                           interface_errors=0, queue_drops=0) for i in range(devices)]
    raw["samples"] = [dict(device_id=f"D{i}",
                           timestamp=(reference - timedelta(seconds=s)).isoformat(),
                           latency_ms=250, jitter_ms=55)
                      for i in range(devices) for s in range(samples)]
    started = perf_counter()
    report = run_report(raw, tmp_path)
    elapsed = perf_counter() - started
    print(f"T29 performance: {devices} x {samples}: {elapsed:.3f}s")
    assert report["cause"] == "latency_degradation"
    assert elapsed < bound


@pytest.mark.parametrize("timestamp", ["2026-31-99T99:00:00Z", "bad-date", "", None, 123, {}])
def test_rqa02_invalid_timestamp_quarantines_whole_sample(timestamp, tmp_path):
    raw = fixture("13")
    raw["samples"][0]["timestamp"] = timestamp
    result = normalize(raw)
    assert result.telemetry["samples"] == []
    assert result.quarantined == [{"device_id": "AP-QA", "field": "timestamp",
                                   "value": timestamp, "reason": "invalid timestamp"}]
    assert result.data_quality.issues.count("1 samples with invalid timestamps excluded") == 1
    assert run_report(raw, tmp_path)["cause"] == "insufficient_evidence"


def test_rqa02_invalid_timestamps_aggregate_and_missing_stays_usable():
    raw = fixture("13")
    raw["samples"] *= 3
    result = normalize(raw)
    assert "3 samples with invalid timestamps excluded" in result.data_quality.issues
    assert len(result.quarantined) == 3
    del raw["samples"][0]["timestamp"]
    assert len(normalize(raw).telemetry["samples"]) == 3


@pytest.mark.parametrize("name", sorted(p.stem for p in (ROOT / "data/scenarios").glob("org_*.json")))
def test_rqa02_organizer_scenarios_unchanged(name, tmp_path):
    expected = json.loads((ROOT / "data/scenarios/labels.json").read_text(encoding="utf-8"))[name]
    report = run_report((ROOT / "data/scenarios" / f"{name}.json").read_text(encoding="utf-8"), tmp_path)
    assert report["cause"] == expected


def test_rqa04_details_keep_overruled_alternatives(tmp_path):
    with TestClient(server.create_app(tmp_path)) as client:
        run_id = client.post("/api/run", json={"scenario": "org_hardware_failure"}).json()["run_id"]
        report = client.get(f"/api/runs/{run_id}/report").json()
        details = client.get(f"/api/runs/{run_id}/details").json()
    state = json.loads((tmp_path / run_id / "state.json").read_text(encoding="utf-8"))
    alternatives = state["findings"]["correlator"]["diagnosis"]["alternatives"]
    overruled = [a for a in alternatives if a.get("overruled_by")]
    assert overruled
    assert report["alternative_causes"]
    for alternative in overruled:
        detail = next(a for a in details["alternatives"] if a["cause"] == alternative["cause"])
        assert detail["overruled_by"] == alternative["overruled_by"]


def render_js(function, setup, checks):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js required for dashboard rendering regression")
    source = (server.STATIC / "app.js").read_text(encoding="utf-8")
    start = source.index(f"function {function}(")
    end = source.index("\n}", start) + 2
    script = setup + "\n" + source[start:end] + f"\n{function}();\n" + checks
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("confidence,limitations", [(0.749, []), (0.9, ["warning-grade devices: AP-QA"])])
def test_rqa05_warning_normal_hero(confidence, limitations, tmp_path):
    report = run_report(fixture("15"), tmp_path)
    assert report["cause"] == "normal" and report["confidence"] < 0.75
    report.update(confidence=confidence, limitations=limitations)
    setup = """
const assert = require('node:assert/strict');
const elements = {};
const $ = (id) => elements[id] ||= {classList: {toggle() {}}};
const state = {report: REPORT, details: null, messages: [], scenariosReady: true};
const inputRejected = () => false;
const isFault = (r) => r && r.status === 'diagnosed' && r.cause !== 'normal';
const PIPELINE_AGENTS = [];
const relativeTime = () => '—';
const counted = () => '—';
""".replace("REPORT", json.dumps(report))
    render_js("renderHero", setup, """
assert.equal(elements['mesh-health'].textContent, 'Campus Mesh: Normal with warnings');
assert.equal(elements['mesh-health'].className, 'flagged');
assert.equal(elements['incident-severity'].textContent, 'Watch');
assert.equal(elements['incident-severity'].className, 'pill flagged');
""")


def test_rqa04_hypothesis_renders_overruling_reason():
    render_js("renderHypotheses", """
const assert = require('node:assert/strict');
const elements = {};
const el = (tag, text, cls) => ({tag, text, cls, children: [],
    append(...items) {this.children.push(...items);}, replaceChildren() {this.children = [];}});
const $ = (id) => elements[id] ||= el('div');
const state = {report: {cause: 'hardware_failure', alternative_causes: ['device_overload']},
    details: {alternatives: [{cause: 'device_overload', confidence: 0.9,
        rationale: 'other evidence', overruled_by: 'a_device_reported_down'}]}};
const inputRejected = () => false;
const label = (s) => s;
const percent = (s) => s;
const humanText = (s) => s?.replaceAll('_', ' ');
const confidenceBar = () => el('div');
""", """
const card = elements.alts.children[0];
const rationale = card.children[2];
assert.equal(rationale.children[0].text, 'Overruled by: ');
assert.equal(rationale.children[1].text, 'a device reported down');
""")


def test_rqa06_nc045_rejected_runs_preserve_quota_and_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "RATE_LIMIT", 1)
    monkeypatch.setattr(server, "MAX_CONCURRENT_RUNS", 0)
    with TestClient(server.create_app(tmp_path)) as client:
        upload_id = client.post("/api/upload", content=b"{}").json()["upload_id"]
        path = tmp_path / "uploads" / f"{upload_id}.json"
        for _ in range(3):
            busy = client.post("/api/run", json={"upload_id": upload_id})
            assert busy.status_code == 429 and "server busy" in busy.json()["detail"]
            assert busy.headers["retry-after"] == "60"
            assert path.exists() and path.with_suffix(".owner").exists()
        monkeypatch.setattr(server, "MAX_CONCURRENT_RUNS", 2)
        started = client.post("/api/run", json={"upload_id": upload_id})
        assert started.status_code == 200
        assert not path.exists() and not path.with_suffix(".owner").exists()
        client.get(f"/api/runs/{started.json()['run_id']}/report")
        limited = client.post("/api/run", json={"scenario": "normal"})
        assert limited.status_code == 429 and "too many runs" in limited.json()["detail"]
        assert limited.headers["retry-after"] == "60"



def test_nc045_rate_limited_upload_can_be_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "RATE_LIMIT", 1)
    with TestClient(server.create_app(tmp_path)) as client:
        run_id = client.post("/api/run", json={"scenario": "normal"}).json()["run_id"]
        client.get(f"/api/runs/{run_id}/report")
        upload_id = client.post("/api/upload", content=b"{}").json()["upload_id"]
        path = tmp_path / "uploads" / f"{upload_id}.json"
        response = client.post("/api/run", json={"upload_id": upload_id})
        assert response.status_code == 429
        assert response.headers["retry-after"] == "60"
        assert path.exists() and path.with_suffix(".owner").exists()
        monkeypatch.setattr(server, "RATE_WINDOW_S", -1)
        retried = client.post("/api/run", json={"upload_id": upload_id})
        assert retried.status_code == 200
        client.get(f"/api/runs/{retried.json()['run_id']}/report")
        assert not path.exists() and not path.with_suffix(".owner").exists()


def test_nc045_failed_run_creation_preserves_upload(tmp_path, monkeypatch):
    with TestClient(server.create_app(tmp_path), raise_server_exceptions=False) as client:
        upload_id = client.post("/api/upload", content=b"{}").json()["upload_id"]
        path = tmp_path / "uploads" / f"{upload_id}.json"
        original = Path.mkdir

        def fail_run_directory(directory, *args, **kwargs):
            if directory.parent == tmp_path:
                raise OSError("run storage unavailable")
            return original(directory, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "mkdir", fail_run_directory)
            assert client.post("/api/run", json={"upload_id": upload_id}).status_code == 500
        assert path.exists() and path.with_suffix(".owner").exists()
        assert client.post("/api/run", json={"upload_id": upload_id}).status_code == 200


def test_rqa07_readme_describes_session_scoped_reopening():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Reopen a run (works in the browser session that started it)" in readme
    assert "Share a run" not in readme
