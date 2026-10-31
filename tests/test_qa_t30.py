"""T30 regressions for exact local windows, recovery, and status conflicts."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
from time import perf_counter

import pytest

from netcoop.agents.correlator import (
    _RunContext,
    _device_down,
    _historical_limitations,
    _instant,
    _local_scopes,
    _score,
    _threshold_grade,
)
from netcoop.agents.normalizer import normalize
from netcoop.cli import _cause, _report, build_orchestrator
from netcoop.config import SIGNATURES


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/qa"
REFERENCE = datetime(2026, 10, 31, 10, tzinfo=timezone.utc)


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def telemetry(samples, devices=None, services=None):
    return {
        "schema_version": "1.0",
        "collected_at": REFERENCE.isoformat(),
        "devices": devices or [],
        "samples": samples,
        "services": services or [],
        "topology": {"nodes": [], "links": []},
    }


def sample(seconds, **metrics):
    return dict(device_id="A", timestamp=(REFERENCE + timedelta(seconds=seconds)).isoformat(),
                **metrics)


def run_report(raw, tmp_path):
    state = asyncio.run(build_orchestrator(tmp_path, no_llm=True).run(raw))
    report = json.loads((tmp_path / state.run_id / "report.json").read_text(encoding="utf-8"))
    return state.findings["correlator"], report


@pytest.mark.parametrize("phase", [0, 37, 120, 150, 299])
@pytest.mark.parametrize("gap", [149, 150, 151, 239, 299, 300, 301])
def test_r3_01_exact_join_is_independent_of_start_phase(phase, gap):
    rows = [sample(phase, latency_ms=250), sample(phase + gap, jitter_ms=55)]
    raw = telemetry(rows)
    scopes = list(_local_scopes(raw, {"A"}, SIGNATURES["latency_degradation"]))
    assert any(all(row in scope["samples"] for row in rows) for scope in scopes) == (gap <= 300)
    assert len(scopes) <= 3
    for scope in scopes:
        stamps = [_instant(row["timestamp"]) for row in scope["samples"]]
        assert max(stamps) - min(stamps) <= 300


@pytest.mark.parametrize("seed", [0, 7, 42])
def test_r3_01_top_three_windows_match_exhaustive_pre_scores(seed):
    rng = random.Random(seed)
    rows = [sample(offset, latency_ms=rng.choice([10, 125, 180, 250]),
                   jitter_ms=rng.choice([1, 25, 40, 55]))
            for offset in rng.sample(range(-1200, 301), 60)]
    signature = SIGNATURES["latency_degradation"]
    anchors = {_instant(row["timestamp"]) for row in rows
               if any(_threshold_grade(metric, row[metric]) > 0 for metric, _ in signature)}
    candidates = []
    for start in anchors | {stamp - 300 for stamp in anchors}:
        window = sorted((row for row in rows if start <= _instant(row["timestamp"]) <= start + 300),
                        key=lambda row: _instant(row["timestamp"]))
        score = sum(weight * max((_threshold_grade(metric, row[metric]) for row in window), default=0)
                    for metric, weight in signature)
        candidates.append((score, start, window))
    expected = sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True)[:3]
    rng.shuffle(rows)
    scopes = list(_local_scopes(telemetry(rows), {"A"}, signature))
    assert [scope["samples"] for scope in scopes] == [window for _, _, window in expected]


@pytest.mark.parametrize("offset", [-301, -300, 0, 300, 301])
def test_r3_01_snapshots_join_only_windows_containing_reference(offset):
    row = sample(offset, latency_ms=250)
    device = dict(device_id="A", status="up", queue_drops=15)
    service = dict(name="dns", server_id="A", status="down")
    untimed = dict(device_id="A", latency_ms=250)
    raw = telemetry([row, untimed], [device], [service])
    signature = [("latency_ms", 0.4), ("queue_drops", 0.3), ("dns_status", 0.3)]
    scopes = list(_local_scopes(raw, {"A"}, signature))
    assert any(row in scope["samples"] and device in scope["devices"]
               and service in scope["services"] for scope in scopes) == (abs(offset) <= 300)
    assert any(scope["samples"] == [untimed] and not scope["devices"]
               and not scope["services"] for scope in scopes)
    assert all(not (row in scope["samples"] and untimed in scope["samples"]) for scope in scopes)


def test_r3_01_missing_reference_uses_newest_sample():
    rows = [sample(-300, latency_ms=250), sample(0, latency_ms=10)]
    device = dict(device_id="A", queue_drops=15)
    raw = telemetry(rows, [device])
    del raw["collected_at"]
    scopes = list(_local_scopes(raw, {"A"}, SIGNATURES["congestion"]))
    assert any(rows[0] in scope["samples"] and device in scope["devices"] for scope in scopes)


def test_r3_01_untimed_samples_join_only_untimed_snapshots():
    device = dict(device_id="A", queue_drops=15)
    row = dict(device_id="A", latency_ms=250)
    raw = telemetry([row], [device])
    del raw["collected_at"]
    scopes = list(_local_scopes(raw, {"A"}, SIGNATURES["congestion"]))
    assert len(scopes) == 1
    assert scopes[0]["samples"] == [row] and scopes[0]["devices"] == [device]


def test_r3_02_siblings_do_not_share_scopes_or_diagnose_congestion(tmp_path):
    raw = fixture("R3-02_sibling_evidence_join")
    scopes = list(_local_scopes(raw, {"AP-A", "AP-B"}, SIGNATURES["congestion"]))
    assert not any({"AP-A", "AP-B"} <= {row["device_id"] for row in scope["samples"]}
                   for scope in scopes)
    _, report = run_report(raw, tmp_path)
    print(f"T30 R3-02: {report['cause']} {report['confidence']}")
    assert report["cause"] in {"latency_degradation", "insufficient_evidence"}
    if report["cause"] == "latency_degradation":
        assert "AP-B" in report["affected_devices"]
        assert "AP-A" not in report["affected_devices"]


@pytest.mark.parametrize("cause", ["congestion", "interface_fault"])
def test_r3_02_direct_uplink_can_corroborate(cause, tmp_path):
    device = dict(device_id="UPLINK", status="up", queue_drops=15, interface_errors=15)
    row = dict(sample(0, link_utilization_pct=97, latency_ms=250, packet_loss_pct=9),
               device_id="AP")
    raw = telemetry([row], [device])
    if cause == "congestion":
        device["interface_errors"] = 0
    else:
        device["queue_drops"] = 0
        row["link_utilization_pct"] = 20
    raw["topology"] = {"nodes": [{"id": "UPLINK"}, {"id": "AP"}],
                       "links": [{"src": "UPLINK", "dst": "AP"}]}
    scopes = list(_local_scopes(raw, {"AP"}, SIGNATURES[cause]))
    assert any(device in scope["devices"] and row in scope["samples"] for scope in scopes)
    assert run_report(raw, tmp_path)[1]["cause"] == cause


def test_rqa03_fixture_14_does_not_bridge_ten_minutes(tmp_path):
    _, report = run_report(fixture("14_ten_minute_bridge"), tmp_path)
    print(f"T30 fixture 14: {report['cause']} {report['confidence']}")
    assert report["confidence"] <= 0.5


def test_r3_05_resolved_anomaly_has_historical_prefix_without_rescoring(tmp_path):
    raw = fixture("R3-03_resolved_anomaly")
    finding, report = run_report(raw, tmp_path)
    notes = [note for note in report["limitations"] if note.startswith("historical: ")]
    print(f"T30 R3-03: {report['cause']} {report['confidence']} {notes}")
    assert notes == ["historical: A anomaly at 2026-10-31T09:00:00Z; latest sample "
                     "2026-10-31T09:58:00Z is healthy (recovered)"]
    assert report["cause"] not in {"normal", "insufficient_evidence"}
    assert notes[0] in finding["notes"]
    context = _RunContext("recovery", "orchestrator", {}, normalize(raw).telemetry, [], 2)
    result = _score(context)
    before = (result.winner.cause, result.winner.confidence)
    assert _historical_limitations(context.telemetry, result.winner, ["A"])
    assert (result.winner.cause, result.winner.confidence) == before


@pytest.mark.parametrize("latest", [dict(latency_ms=250, jitter_ms=1),
                                   dict(latency_ms=11, jitter_ms=55),
                                   dict(throughput_mbps=20)])
def test_r3_05_unrecovered_or_unmeasured_latest_sample_is_not_healthy(latest, tmp_path):
    raw = fixture("R3-03_resolved_anomaly")
    raw["samples"][-1] = dict(device_id="A", timestamp="2026-10-31T09:58:00Z", **latest)
    _, report = run_report(raw, tmp_path)
    assert not any(note.startswith("historical: ") for note in report["limitations"])


def test_r3_05_current_snapshot_support_does_not_claim_recovery(tmp_path):
    raw = fixture("R3-03_resolved_anomaly")
    raw["devices"][0].update(status="down", cpu_pct=99, interface_errors=15)
    _, report = run_report(raw, tmp_path)
    assert not any(note.startswith("historical: ") for note in report["limitations"])


SCENARIOS = json.loads((ROOT / "data/scenarios/labels.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name,expected", sorted(SCENARIOS.items()))
def test_r3_05_bundled_scenarios_keep_labels_without_historical_notes(name, expected, tmp_path):
    raw = (ROOT / "data/scenarios" / f"{name}.json").read_text(encoding="utf-8")
    state = asyncio.run(build_orchestrator(tmp_path, no_llm=True).run(raw))
    report = _report(state)
    assert _cause(state, report) == expected
    assert not any(note.startswith("historical: ") for note in report.limitations)


@pytest.mark.parametrize("reverse", [False, True])
def test_r3_07_conflicting_down_status_cannot_trigger_domain_override(reverse, tmp_path):
    raw = fixture("R3-08_conflicting_device_status")
    if reverse:
        raw["devices"].reverse()
    assert not _device_down(normalize(raw).telemetry)
    finding, report = run_report(raw, tmp_path)
    print(f"T30 R3-08: {report['cause']} {report['confidence']}")
    assert any("conflicting status for duplicate device_id=A" in note for note in report["limitations"])
    notes = [*report["limitations"], *finding["diagnosis"]["conflicts"]]
    assert not any("Conflict resolved by domain rule: a device reported down" in note for note in notes)
    assert all(item.get("overruled_by") != "a device reported down"
               for item in finding["diagnosis"]["alternatives"])


def test_r3_07_unambiguous_down_device_remains_decisive():
    raw = fixture("R3-08_conflicting_device_status")
    assert not _device_down(raw)
    raw["devices"].append(dict(device_id="B", status="down"))
    assert _device_down(raw)
    assert _device_down({"devices": [dict(device_id="A", status="down")] * 2})


@pytest.mark.performance
@pytest.mark.parametrize("devices,count,bound", [(1, 4000, 1), (5, 3600, 5)])
def test_r3_01_dense_sample_performance(devices, count, bound, tmp_path):
    rows = [dict(sample(-seconds, latency_ms=250, jitter_ms=55), device_id=f"D{i}")
            for i in range(devices) for seconds in range(count)]
    raw = telemetry(rows, [dict(device_id=f"D{i}", status="up", cpu_pct=20, mem_pct=20,
                                interface_errors=0, queue_drops=0) for i in range(devices)])
    started = perf_counter()
    _, report = run_report(raw, tmp_path)
    elapsed = perf_counter() - started
    print(f"T30 performance: {devices} x {count}: {elapsed:.3f}s (bound {bound}s)")
    assert report["cause"] == "latency_degradation"
    assert elapsed < bound
