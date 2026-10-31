"""T32: untimed scopes, independently affected upstreams, and incident severity."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from netcoop.agents.correlator import (
    CorrelatorAgent, _RunContext, _candidate, _incident_severity, _local_scopes,
)
from netcoop.cli import _report, build_orchestrator
from netcoop.config import SIGNATURES
from netcoop.protocol import Report


ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8-sig"))


def run_report(raw, tmp_path):
    state = asyncio.run(build_orchestrator(tmp_path, no_llm=True).run(raw))
    return state, _report(state)


@pytest.mark.parametrize("reverse", [False, True])
def test_fqa01_untimed_rows_do_not_fuse(reverse, tmp_path):
    raw = load("tests/fixtures/final_qa/FQA01_no_timestamps_cross_record_correlation.json")
    if reverse:
        raw["samples"].reverse()
    scopes = list(_local_scopes(raw, {"A"}, SIGNATURES["congestion"]))
    assert len(scopes) == 2
    assert all(len(scope["samples"]) == 1 and scope["devices"] == raw["devices"]
               for scope in scopes)
    candidate = _candidate("congestion", raw, {}, 1.0, [], {})
    assert not (candidate.observations["link_utilization_pct"].grade > 0
                and candidate.observations["latency_ms"].grade > 0)
    _, report = run_report(raw, tmp_path)
    assert not (report.cause == "congestion" and report.confidence >= 0.75)
    assert any(note.startswith("untimed: no timestamps or collected_at, so co-occurrence of ")
               for note in report.limitations)


def test_untimed_full_signature_caps_confidence_and_preserves_snapshots():
    raw = dict(devices=[dict(device_id="A", queue_drops=300)],
               samples=[dict(device_id="A", link_utilization_pct=98, latency_ms=320)],
               services=[dict(server_id="A", name="dns", status="down")])
    scope, = _local_scopes(raw, {"A"}, SIGNATURES["congestion"])
    assert scope["services"] == raw["services"]
    candidate = _candidate("congestion", raw, {}, 1.0, [], {"performance": "congestion"})
    assert candidate.confidence == 0.749
    assert "latency_ms, link_utilization_pct, queue_drops" in candidate.limitations[0]
    # Collection time dates the snapshots, so they cannot join untimed samples.
    raw["collected_at"] = "2026-10-31T10:00:00Z"
    assert all(not scope["samples"] or not scope["devices"]
               for scope in _local_scopes(raw, {"A"}, SIGNATURES["congestion"]))


def test_untimed_samples_on_adjacent_devices_stay_separate():
    raw = dict(devices=[dict(device_id="A", queue_drops=300)],
               samples=[dict(device_id="A", link_utilization_pct=98),
                        dict(device_id="B", latency_ms=320)], services=[],
               topology=dict(nodes=[dict(id="A"), dict(id="B")],
                             links=[dict(src="A", dst="B")]))
    assert all(len(scope["samples"]) <= 1
               for scope in _local_scopes(raw, {"A", "B"}, SIGNATURES["congestion"]))


def test_fqa02_healthy_upstream_is_context(tmp_path):
    raw = load("tests/fixtures/final_qa/FQA02_unaffected_upstream.json")
    state, report = run_report(raw, tmp_path)
    assert report.affected_devices == ["AP-A"]
    note = "upstream context: CORE (no abnormal telemetry; topological parent of AP-A)"
    assert note in report.limitations
    assert note in state.findings["correlator"]["notes"]


@pytest.mark.parametrize("abnormal", ["cpu", "sample", "down", "service", "service_metric"])
def test_upstream_requires_own_abnormal_evidence(abnormal):
    raw = load("tests/fixtures/final_qa/FQA02_unaffected_upstream.json")
    if abnormal == "cpu":
        raw["devices"][0]["cpu_pct"] = 85
    elif abnormal == "sample":
        raw["samples"].append(dict(device_id="CORE", packet_loss_pct=2))
    elif abnormal == "down":
        raw["devices"][0]["status"] = "down"
    elif abnormal == "service":
        raw["services"] = [dict(server_id="CORE", name="dns", status="down")]
    else:
        raw["services"] = [dict(server_id="CORE", name="dns", success_rate_pct=90)]
    # Use AP-A's winning scope: the upstream must qualify on its own telemetry,
    # even if that telemetry is outside the winning signature.
    own = deepcopy(raw)
    own["devices"] = own["devices"][1:]
    own["samples"] = own["samples"][:1]
    own["services"] = []
    winner = _candidate("latency_degradation", own, {}, 1.0, [], {})
    findings = {"connectivity": {"evidence": [dict(metric="common_upstream", device_id="CORE")]}}
    context = _RunContext("test", "orchestrator", findings, raw, [], 2)
    assert CorrelatorAgent._affected_devices(context, winner) == ["AP-A", "CORE"]
    assert not any(note.startswith("upstream context:") for note in winner.limitations)


@pytest.mark.parametrize("cause,telemetry,expected", [
    ("latency_degradation", dict(samples=[dict(device_id="A", latency_ms=199, jitter_ms=49)]), "warning"),
    ("latency_degradation", dict(samples=[dict(device_id="A", latency_ms=200)]), "critical"),
    ("weak_wifi", dict(samples=[dict(device_id="A", rssi_dbm=-75)]), "critical"),
    ("device_overload", dict(devices=[dict(device_id="A", status="down")]), "critical"),
    ("device_overload", dict(devices=[dict(device_id="A", status="down"), dict(device_id="A", status="up")]), "warning"),
    ("latency_degradation", dict(services=[dict(server_id="A", name="dns", status="down")]), "critical"),
    ("latency_degradation", dict(samples=[dict(device_id="OTHER", latency_ms=300)]), "warning"),
    ("latency_degradation", dict(devices=[dict(device_id="A", cpu_pct=99)]), "warning"),
    ("normal", dict(devices=[dict(device_id="A", status="down")]), None),
    ("insufficient_evidence", dict(devices=[dict(device_id="A", status="down")]), None),
    ("error", {}, None),
])
def test_severity_uses_affected_signature_metrics_and_unambiguous_down(cause, telemetry, expected):
    assert _incident_severity(cause, telemetry, ["A"]) == expected


def test_warn_only_fault_has_warning_severity_in_json_and_markdown(tmp_path):
    raw = load("tests/fixtures/qa/06_warn_only_in_healthy_fleet.json")
    state, report = run_report(raw, tmp_path)
    assert report.cause not in {"normal", "insufficient_evidence", None}
    assert report.severity == "warning"
    directory = tmp_path / state.run_id
    assert json.loads((directory / "report.json").read_text(encoding="utf-8"))["severity"] == "warning"
    assert "Severity: Warning" in (directory / "report.md").read_text(encoding="utf-8")
    legacy = report.model_dump()
    legacy.pop("severity")
    assert Report.model_validate(legacy).severity is None


SEVERITIES = {
    "conflicting_evidence": None, "congestion": "critical", "device_overload": "critical",
    "dhcp_failure": "critical", "dns_failure": "critical", "insufficient_data": None,
    "interface_fault": "critical", "interop_renamed_fields_units": "critical",
    "malformed_json": None, "missing_sections": None, "normal": None,
    "org_hardware_failure": "critical", "org_latency_spike": "critical", "org_normal": None,
    "schema_v0_legacy": "critical", "weak_wifi": "critical",
}


@pytest.mark.parametrize("name,expected", sorted(SEVERITIES.items()))
def test_bundled_scenario_severity(name, expected, tmp_path):
    raw = (ROOT / "data/scenarios" / f"{name}.json").read_text(encoding="utf-8-sig")
    state, report = run_report(raw, tmp_path)
    assert report.severity == expected
    if expected:
        assert f"Severity: {expected.title()}" in (tmp_path / state.run_id / "report.md").read_text(encoding="utf-8")
    if name == "congestion":
        telemetry = load("data/scenarios/congestion.json")
        core = next(row for row in telemetry["devices"] if row["device_id"] == "CORE_RTR_01")
        sample = next(row for row in telemetry["samples"] if row["device_id"] == "CORE_RTR_01")
        # 275 drops >= crit 10 and 94% utilization >= crit 90.
        # Latency is 185 ms, below its critical threshold of 200 ms.
        assert core["queue_drops"] == 275
        assert sample["link_utilization_pct"] == 94
        assert sample["latency_ms"] == 185


@pytest.mark.parametrize("name,expected,parent", [
    ("device_overload", "ACADEMIC_DIST_SW_01", "CORE_RTR_01"),
    ("dhcp_failure", "DHCP_SRV_01", "CORE_RTR_01"),
    ("dns_failure", "DNS_SRV_01", "CORE_RTR_01"),
    ("interface_fault", "DEPT_SW_01", "ACADEMIC_DIST_SW_01"),
    ("weak_wifi", "HOSTEL_AP_01", "HOSTEL_DIST_SW_01"),
])
def test_bundled_healthy_upstream_is_only_context(name, expected, parent, tmp_path):
    _, report = run_report(load(f"data/scenarios/{name}.json"), tmp_path)
    assert report.affected_devices == [expected]
    assert (f"upstream context: {parent} (no abnormal telemetry; "
            f"topological parent of {expected})") in report.limitations
