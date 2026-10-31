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
