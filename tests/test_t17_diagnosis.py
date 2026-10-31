"""T17 regression tests: B9 (alternatives below winner), B10 (down device vs normal), B11 (abstain)."""
import asyncio
import json
from pathlib import Path

import pytest

from netcoop.agents.connectivity import analyze_connectivity
from netcoop.cli import _cause, _report, build_orchestrator

SCENARIOS = sorted((Path(__file__).resolve().parents[1] / "data" / "scenarios").glob("*.json"))
SCENARIOS = [p for p in SCENARIOS if p.stem != "labels"]


def _run(tmp_path, raw):
    state = asyncio.run(build_orchestrator(tmp_path).run(raw))
    report = _report(state)
    return state, report, _cause(state, report)


def _diagnosis(state):
    return state.findings["correlator"]["diagnosis"]


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_alternatives_strictly_below_chosen_confidence(tmp_path, path):
    state, _, _ = _run(tmp_path, path.read_text(encoding="utf-8"))
    if isinstance(state, dict):  # run ended in an error report: no diagnosis to check
        return
    diagnosis = _diagnosis(state)
    for alt in diagnosis["alternatives"]:
        assert alt["confidence"] < diagnosis["confidence"], (alt, diagnosis["confidence"])


def test_domain_rule_override_discloses_higher_raw_score(tmp_path):
    path = next(p for p in SCENARIOS if p.stem == "org_hardware_failure")
    state, report, cause = _run(tmp_path, path.read_text(encoding="utf-8"))
    diagnosis = _diagnosis(state)
    assert cause == "hardware_failure"
    assert all(alt["confidence"] < diagnosis["confidence"] for alt in diagnosis["alternatives"])
    assert any("selected by domain rule over higher raw score" in item
               for item in report.limitations)


DOWN_NO_TOPOLOGY = {
    "schema_version": "1.0", "incident_id": "I", "collected_at": "2026-10-31T09:00:00Z",
    "devices": [{"device_id": "A", "status": "down"}, {"device_id": "B", "status": "up"}],
    "samples": [
        {"device_id": "A", "timestamp": "2026-10-31T09:00:00Z", "packet_loss_pct": 100, "latency_ms": 50},
        {"device_id": "B", "timestamp": "2026-10-31T09:00:00Z", "packet_loss_pct": 0, "latency_ms": 20},
    ],
}


def test_down_device_without_topology_is_hardware_failure(tmp_path):
    _, report, cause = _run(tmp_path, json.dumps(DOWN_NO_TOPOLOGY))
    assert cause == "hardware_failure"
    assert report.affected_devices == ["A"]


def test_normal_is_ineligible_with_a_down_device(tmp_path):
    data = json.loads(json.dumps(DOWN_NO_TOPOLOGY))
    for sample in data["samples"]:
        sample["packet_loss_pct"], sample["latency_ms"] = 0, 10
    _, _, cause = _run(tmp_path, json.dumps(data))
    assert cause != "normal"


def test_normal_is_ineligible_with_a_critical_metric(tmp_path):
    data = {
        "schema_version": "1.0", "incident_id": "I", "collected_at": "2026-10-31T09:00:00Z",
        "devices": [{"device_id": f"D{i}", "status": "up", "cpu_pct": 10, "mem_pct": 10}
                    for i in range(8)] + [{"device_id": "D8", "status": "up", "cpu_pct": 99, "mem_pct": 10}],
        "samples": [{"device_id": f"D{i}", "timestamp": "2026-10-31T09:00:00Z", "latency_ms": 10,
                     "packet_loss_pct": 0} for i in range(9)],
    }
    _, _, cause = _run(tmp_path, json.dumps(data))
    assert cause != "normal"


def test_connectivity_abstains_without_topology_and_services():
    finding = analyze_connectivity({
        "devices": [{"device_id": "A", "status": "down"}],
        "samples": [{"device_id": "A", "packet_loss_pct": 50, "latency_ms": 500}],
        "topology": {"nodes": [], "links": []}, "services": [],
    })
    assert finding.hypotheses == []
    assert any("no topology/services supplied" in note for note in finding.notes)


def test_abstaining_connectivity_does_not_vote_on_organizer_records(tmp_path):
    path = next(p for p in SCENARIOS if p.stem == "org_hardware_failure")
    state, report, cause = _run(tmp_path, path.read_text(encoding="utf-8"))
    assert cause == "hardware_failure"
    assert state.findings["connectivity"]["hypotheses"] == []
    assert not any("connectivity=" in item for item in report.limitations)


def test_agreement_weights_votes_by_domain_relevance():
    from netcoop.agents.correlator import _agreement_factor

    tops = {"performance": "latency_degradation", "infrastructure": "congestion"}
    # performance is the specialist for latency_degradation, infrastructure for congestion-adjacent causes
    assert _agreement_factor("latency_degradation", tops) > _agreement_factor("congestion", tops)
    assert _agreement_factor("latency_degradation", {"performance": "latency_degradation"}) == 1.0
    assert _agreement_factor("normal", {}) == 0.7


def test_large_healthy_input_is_normal_and_fast(tmp_path):
    import time

    from data.generator.organizer_generator import generate_telemetry

    records = []
    for index in range(20_000):
        record = generate_telemetry("normal", seed=index % 50)
        record["device_id"] = f"SW{index}"
        records.append(record)
    started = time.perf_counter()
    _, _, cause = _run(tmp_path, json.dumps(records))
    assert cause == "normal"
    assert time.perf_counter() - started < 5.0


def test_quality_details_scales_linearly_with_many_issues():
    import time

    from netcoop.agents.correlator import _quality_details

    issues = [f"missing field organizer[{i}].timestamp" for i in range(50_000)]
    started = time.perf_counter()
    _quality_details({"normalizer": {"data_quality": {"score": 0.5, "issues": issues}}})
    assert time.perf_counter() - started < 1.0
