"""T18 regression tests: N1-N7 and backend endpoints A1-A6."""
import asyncio
import json
from pathlib import Path

from netcoop.agents.normalizer import normalize
from netcoop.cli import _cause, _report, build_orchestrator
from data.generator.organizer_generator import generate_telemetry

SCENARIOS = Path(__file__).resolve().parents[1] / "data" / "scenarios"


def _run(tmp_path, raw):
    state = asyncio.run(build_orchestrator(tmp_path).run(raw))
    report = _report(state)
    return state, report, _cause(state, report)


def _org_records(n, **extra):
    records = []
    for index in range(n):
        record = generate_telemetry("normal", seed=index % 50)
        record["device_id"] = f"SW{index}"
        record.update(extra)
        records.append(record)
    return records


# N1 -------------------------------------------------------------------------
def test_n1_extra_field_on_100_healthy_records_stays_normal(tmp_path):
    _, report, cause = _run(tmp_path, json.dumps(_org_records(100, vendor="x")))
    assert cause == "normal" and report.confidence >= 0.6


def test_n1_quality_does_not_collapse_with_record_count():
    scores = [normalize(json.dumps(_org_records(n, vendor="x"))).data_quality.score
              for n in (1, 10, 100)]
    assert min(scores) >= 0.8


def test_n1_repeated_issues_are_aggregated_and_capped():
    issues = normalize(json.dumps(_org_records(100, vendor="x"))).data_quality.issues
    vendor = [i for i in issues if "vendor" in i]
    assert len(vendor) == 1 and "100/100" in vendor[0]
    many = {f"field{i}": 1 for i in range(60)}
    capped = normalize(json.dumps(_org_records(3, **many))).data_quality.issues
    assert len(capped) <= 26 and "more" in capped[-1]


def test_n1_missing_timestamp_on_healthy_records_is_minor(tmp_path):
    records = [{k: v for k, v in r.items() if k != "timestamp"} for r in _org_records(100)]
    assert normalize(json.dumps(records)).data_quality.score >= 0.7
    _, _, cause = _run(tmp_path, json.dumps(records))
    assert cause == "normal"


# N2 / N3 / A1 ---------------------------------------------------------------
def test_n2_congestion_affects_only_related_devices(tmp_path):
    _, report, cause = _run(tmp_path, (SCENARIOS / "congestion.json").read_text(encoding="utf-8"))
    assert cause == "congestion"
    assert "CORE_RTR_01" in report.affected_devices
    assert "LECTURE_AP_01" not in report.affected_devices


def test_n3_evidence_carries_device_unit_threshold_baseline(tmp_path):
    _, report, _ = _run(tmp_path, (SCENARIOS / "congestion.json").read_text(encoding="utf-8"))
    util = next(e for e in report.supporting_evidence if e.metric == "link_utilization_pct")
    assert util.device_id == "CORE_RTR_01" and util.unit == "%" and util.threshold == 75.0
    assert util.baseline is not None and util.baseline < util.value
    assert "CORE_RTR_01" in util.interpretation and "critical 90" in util.interpretation


# N4 -------------------------------------------------------------------------
def test_n4_vercel_json_sets_security_headers_with_font_csp():
    config = json.loads((SCENARIOS.parents[1] / "vercel.json").read_text(encoding="utf-8"))
    headers = {h["key"]: h["value"] for h in config["headers"][0]["headers"]}
    assert headers["X-Frame-Options"] == "DENY" and headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    csp = headers["Content-Security-Policy"]
    assert "https://fonts.googleapis.com" in csp and "font-src https://fonts.gstatic.com" in csp
    from api.server import SECURITY_HEADERS
    assert SECURITY_HEADERS["Content-Security-Policy"] == csp


# N5 -------------------------------------------------------------------------
def test_n5_no_domain_rule_claim_when_insufficient(tmp_path):
    inputs = [(SCENARIOS / f"{name}.json").read_text(encoding="utf-8")
              for name in ("conflicting_evidence", "insufficient_data")]
    inputs.append(json.dumps([{"device_id": "X", "timestamp": "2026-10-31T09:00:00Z",
                               "metrics": {"cpu_utilization_pct": 99.9}, "status": "Critical"}]))
    seen_insufficient = 0
    for raw in inputs:
        _, report, _ = _run(tmp_path, raw)
        if report.status == "insufficient_evidence":
            seen_insufficient += 1
            assert not any(i.startswith(("Conflict resolved by domain rule", "selected by"))
                           for i in report.limitations)
    assert seen_insufficient >= 2


# N6 -------------------------------------------------------------------------
def test_n6_connectivity_does_not_vote_interface_fault_on_congestion(tmp_path):
    state, report, cause = _run(tmp_path, (SCENARIOS / "congestion.json").read_text(encoding="utf-8"))
    hypotheses = state.findings["connectivity"]["hypotheses"]
    top = max(hypotheses, key=lambda h: h["score"])["cause"]
    assert cause == "congestion" and top != "interface_fault"
    assert not any("connectivity=interface_fault" in i for i in report.limitations)


# N7 -------------------------------------------------------------------------
def test_n7_organizer_latency_spike_sweep(tmp_path):
    hits = 0
    for seed in range(60):
        _, _, cause = _run(tmp_path, json.dumps(generate_telemetry("latency_spike", seed=seed)))
        hits += cause == "latency_degradation"
    assert hits >= 58
