"""T23 fixture regressions: invalid evidence never becomes a diagnosis."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from netcoop.agents.normalizer import normalize
from netcoop.cli import build_orchestrator


FIXTURES = Path(__file__).parent / "fixtures" / "qa"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def run_report(raw, tmp_path):
    state = asyncio.run(build_orchestrator(tmp_path, no_llm=True).run(raw))
    report = json.loads((tmp_path / state.run_id / "report.json").read_text(encoding="utf-8"))
    return state, report


def test_nc002_impossible_values_are_itemized_and_excluded(tmp_path):
    raw = fixture("02_impossible_values.json")
    result = normalize(raw)
    assert result.error is None
    sample = result.telemetry["samples"][0]
    assert sample["link_utilization_pct"] is None
    assert sample["packet_loss_pct"] is None
    assert sample["latency_ms"] == 300
    assert {(q["device_id"], q["field"], q["value"]) for q in result.quarantined} == {
        ("SW1", "link_utilization_pct", 150), ("SW1", "packet_loss_pct", 110),
    }
    assert all(q["reason"] for q in result.quarantined)
    assert raw["samples"][0]["packet_loss_pct"] == 110  # Input is untouched.
    state, report = run_report(raw, tmp_path)
    assert state.findings["normalizer"]["quarantined"] == result.quarantined
    assert report["cause"] == "insufficient_evidence"
    for field in ("link_utilization_pct", "packet_loss_pct"):
        assert any(field in issue and "quarantined" in issue for issue in report["limitations"])


@pytest.mark.parametrize("section,field,value", [
    ("devices", "cpu_pct", -1), ("devices", "mem_pct", 101),
    ("devices", "queue_drops", -1), ("devices", "interface_errors", -2),
    ("samples", "latency_ms", -3), ("samples", "jitter_ms", -4),
    ("samples", "rssi_dbm", 1), ("samples", "throughput_mbps", -1),
    ("samples", "retransmission_pct", 101),
])
def test_nc002_each_impossible_metric_becomes_none(section, field, value):
    raw = fixture("10_version_field_self_schema.json")
    raw[section][0][field] = value
    result = normalize(raw)
    assert result.error is None
    assert result.telemetry[section][0][field] is None
    assert any(q["field"] == field and q["value"] == value for q in result.quarantined)


@pytest.mark.parametrize("name,reason", [
    ("03_all_samples_six_years_old.json", "stale"),
    ("07_future_dated_telemetry.json", "future"),
])
def test_nc003_collected_at_rejects_stale_and_future_samples(name, reason, tmp_path):
    raw = fixture(name)
    result = normalize(raw)
    assert result.error is None
    assert result.telemetry["samples"] == []
    assert result.quarantined[0]["field"] == "timestamp"
    assert reason in result.quarantined[0]["reason"]
    _, report = run_report(raw, tmp_path)
    assert report["cause"] == "insufficient_evidence"
    if reason == "stale":
        assert any("samples older than the analysis window" in issue for issue in report["limitations"])
    else:
        assert any(reason in issue and "quarantined" in issue for issue in report["limitations"])


def test_nc003_time_window_boundaries_and_newest_sample_fallback():
    raw = fixture("03_all_samples_six_years_old.json")
    raw["samples"] = [dict(raw["samples"][0], timestamp=stamp) for stamp in (
        "2026-10-31T07:59:59Z", "2026-10-31T08:00:00Z",
        "2026-10-31T09:05:00Z", "2026-10-31T09:05:01Z",
    )]
    result = normalize(raw)
    assert [s["timestamp"] for s in result.telemetry["samples"]] == [
        "2026-10-31T08:00:00Z", "2026-10-31T09:05:00Z",
    ]
    # The newest historical sample is the reference when collected_at is absent.
    del raw["collected_at"]
    raw["samples"] = [dict(raw["samples"][0], timestamp=stamp) for stamp in (
        "2020-01-01T07:59:59Z", "2020-01-01T08:00:00Z", "2020-01-01T09:00:00Z",
    )]
    result = normalize(raw)
    assert len(result.telemetry["samples"]) == 2
    assert result.telemetry["samples"][-1]["timestamp"] == "2020-01-01T09:00:00Z"


def test_nc004_same_instant_merges_complementary_fields_and_nulls_conflicts():
    raw = fixture("04_same_timestamp_complementary_metrics.json")
    result = normalize(raw)
    assert result.error is None
    assert result.telemetry["samples"] == [{
        "device_id": "AP1", "timestamp": "2026-10-31T09:00:00Z",
        "latency_ms": 10, "packet_loss_pct": 9.5, "link_utilization_pct": 91,
    }]
    raw["samples"].extend([
        dict(raw["samples"][0], latency_ms=20),
        dict(raw["samples"][0], latency_ms=10),
    ])
    for rows in (raw["samples"], list(reversed(raw["samples"]))):
        result = normalize(dict(raw, samples=rows))
        assert result.telemetry["samples"][0]["latency_ms"] is None
        assert result.telemetry["samples"][0]["packet_loss_pct"] == 9.5
        assert any("conflict" in issue for issue in result.data_quality.issues)


def test_nc005_alias_conflicts_are_order_independent(tmp_path):
    results, diagnoses = [], []
    for order in ("canonical_first", "alias_first"):
        raw = fixture(f"05_conflicting_alias_{order}.json")
        result = normalize(raw)
        assert result.telemetry["samples"][0]["latency_ms"] is None
        assert any("ambiguous" in issue for issue in result.data_quality.issues)
        results.append(result)
        state, report = run_report(raw, tmp_path / order)
        diagnoses.append(state.findings["correlator"]["diagnosis"])
        assert any("ambiguous" in issue for issue in report["limitations"])
    assert results[0].telemetry["samples"] == results[1].telemetry["samples"]
    assert results[0].data_quality == results[1].data_quality
    assert diagnoses[0] == diagnoses[1]


@pytest.mark.parametrize("organizer", [False, True])
@pytest.mark.parametrize("alias_value,expected", [(0.01, 10), (0.01005, 10), (1.2, None)])
def test_nc005_converted_aliases_and_canonical_precedence(organizer, alias_value, expected):
    raw = fixture("05_conflicting_alias_canonical_first.json")
    metrics = {"latency_ms": 10, "latency_s": alias_value, "lat": 10}
    for values in (metrics, dict(reversed(list(metrics.items())))):
        if organizer:
            payload = {"device_id": "AP1", "timestamp": raw["collected_at"], "metrics": values}
        else:
            payload = deepcopy(raw)
            payload["samples"][0] = {"device_id": "AP1", "timestamp": raw["collected_at"], **values}
        result = normalize(payload)
        assert result.error is None
        assert result.telemetry["samples"][0]["latency_ms"] == expected


def test_nc013_version_alias_has_full_quality():
    raw = fixture("10_version_field_self_schema.json")
    result = normalize(raw)
    assert result.error is None
    assert result.telemetry["schema_version"] == "1.0"
    assert result.data_quality.score == 1.0
    assert result.data_quality.issues == []
    raw["version"] = "999"
    assert "unknown schema_version" in normalize(raw).error


def test_nc014_dedupe_uses_utc_instant_not_timestamp_spelling():
    raw = fixture("04_same_timestamp_complementary_metrics.json")
    raw["samples"][1]["timestamp"] = "2026-10-31T14:30:00+05:30"
    raw["samples"].append(dict(raw["samples"][0], timestamp="2026-10-31T09:00:00.000+00:00"))
    result = normalize(raw)
    assert result.error is None
    assert len(result.telemetry["samples"]) == 1
    assert result.telemetry["samples"][0]["packet_loss_pct"] == 9.5
    assert result.telemetry["samples"][0]["latency_ms"] == 10


def test_nc025_valid_coverage_gates_confidence_despite_penalty_cap(tmp_path):
    raw = fixture("02_impossible_values.json")
    result = normalize(raw)
    assert result.data_quality.score < 0.3
    assert any("valid-metric coverage: 50.0%" in issue for issue in result.data_quality.issues)
    _, report = run_report(raw, tmp_path)
    assert report["confidence"] < 0.3
    assert report["status"] == "insufficient_evidence"
    assert report["cause"] == "insufficient_evidence"
