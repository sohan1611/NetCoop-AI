"""T35 regressions: unit-bearing numbers, UTF-8 BOMs, and epoch timestamps."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from netcoop.agents.normalizer import normalize
from netcoop.cli import build_orchestrator

ROOT = Path(__file__).resolve().parents[1]
ISO_TIMESTAMP = "2026-10-31T09:00:00Z"


def congestion():
    return json.loads((ROOT / "data/scenarios/congestion.json").read_text(encoding="utf-8"))


def run_report(raw, tmp_path):
    state = asyncio.run(build_orchestrator(tmp_path, no_llm=True).run(raw))
    report = json.loads((tmp_path / state.run_id / "report.json").read_text(encoding="utf-8"))
    return state, report


@pytest.mark.parametrize("section,key,value,target,expected", [
    ("samples", "latency_ms", "0.3 s", "latency_ms", 300.0),
    ("samples", "latency_ms", "400us", "latency_ms", 0.4),
    ("samples", "latency_ms", "320 ms", "latency_ms", 320.0),
    ("samples", "latency_ms", "0.3 SEC", "latency_ms", 300.0),
    ("samples", "latency_ms", "0.3 secs", "latency_ms", 300.0),
    ("samples", "latency_ms", "400µs", "latency_ms", 0.4),
    ("samples", "latency_ms", "400μs", "latency_ms", 0.4),
    ("samples", "bandwidth_mbps", "1 Gbps", "bandwidth_mbps", 1000.0),
    ("samples", "bandwidth_mbps", "1 gb/s", "bandwidth_mbps", 1000.0),
    ("samples", "bandwidth_mbps", "1000 kbps", "bandwidth_mbps", 1.0),
    ("samples", "bandwidth_mbps", "1000 kb/s", "bandwidth_mbps", 1.0),
    ("samples", "bandwidth_mbps", "1000000 bps", "bandwidth_mbps", 1.0),
    ("samples", "bandwidth_mbps", "1 mbps", "bandwidth_mbps", 1.0),
    ("samples", "bandwidth_mbps", "1 mb/s", "bandwidth_mbps", 1.0),
    ("samples", "bandwidth_mbps", "1 mbit/s", "bandwidth_mbps", 1.0),
    ("samples", "link_utilization_pct", "98 %", "link_utilization_pct", 98.0),
    ("samples", "packet_loss_frac", "50%", "packet_loss_pct", 50.0),
    ("samples", "packet_loss_pct", "50 pct", "packet_loss_pct", 50.0),
    ("samples", "packet_loss_pct", "50 percent", "packet_loss_pct", 50.0),
    ("samples", "rssi_dbm", "-60 dBm", "rssi_dbm", -60.0),
    ("devices", "queue_drops", "275 drops", "queue_drops", 275.0),
    ("samples", "latency_s", "320 ms", "latency_ms", 320.0),
    ("samples", "bandwidth_kbps", "1 Gbps", "bandwidth_mbps", 1000.0),
    ("samples", "latency_s", "0.3", "latency_ms", 300.0),
    ("samples", "bandwidth_kbps", "1000", "bandwidth_mbps", 1.0),
    ("samples", "packet_loss_frac", "0.5", "packet_loss_pct", 50.0),
])
def test_unit_numeric_strings(section, key, value, target, expected, tmp_path):
    raw = congestion()
    raw[section][0].pop(target, None)
    raw[section][0][key] = value
    state, report = run_report(raw, tmp_path)
    assert state.telemetry[section][0][target] == pytest.approx(expected)
    issues = state.findings["normalizer"]["data_quality"]["issues"]
    assert any("coerced numeric string" in issue and repr(value) in issue for issue in issues)
    if (key, value) == ("latency_ms", "0.3 s"):
        assert any("converted x1000" in issue for issue in issues)
    assert report["cause"]


@pytest.mark.parametrize("key,value,unit,suffix", [
    ("latency_ms", "5 parsecs", "ms", "parsecs"),
    ("bandwidth_mbps", "5 ms", "mbps", "ms"),
    ("link_utilization_pct", "5 s", "pct", "s"),
    ("rssi_dbm", "-5 percent", "dbm", "percent"),
])
def test_incompatible_units_are_dropped(key, value, unit, suffix, tmp_path):
    raw = congestion()
    raw["samples"][0][key] = value
    state, _ = run_report(raw, tmp_path)
    assert key not in state.telemetry["samples"][0]
    assert (f"unit {suffix!r} at root.samples[0].{key} is not convertible to {unit}; value dropped"
            in state.findings["normalizer"]["data_quality"]["issues"])


@pytest.mark.parametrize("as_bytes", [False, True])
def test_bom_preserves_congestion_diagnosis(as_bytes, tmp_path):
    text = (ROOT / "data/scenarios/congestion.json").read_text(encoding="utf-8")
    raw = "\ufeff" + text
    if as_bytes:
        raw = raw.encode("utf-8")
    assert normalize(raw).telemetry == normalize(text).telemetry
    _, expected = run_report(text, tmp_path)
    _, actual = run_report(raw, tmp_path)
    assert actual["cause"] == expected["cause"] == "congestion"
    assert actual["confidence"] == expected["confidence"]


@pytest.mark.parametrize("epoch", [1793437200, 1793437200.0, 1793437200000,
                                   1793437200000.0, "1793437200", "1793437200000"])
@pytest.mark.parametrize("epoch_collected_at", [False, True])
def test_epoch_timestamps_preserve_diagnosis(epoch, epoch_collected_at, tmp_path):
    raw = congestion()
    _, expected = run_report(raw, tmp_path)
    for sample in raw["samples"]:
        sample["timestamp"] = epoch
    if epoch_collected_at:
        raw["collected_at"] = epoch
    state, actual = run_report(raw, tmp_path)
    assert actual["cause"] == expected["cause"] == "congestion"
    assert actual["confidence"] == expected["confidence"]
    assert all(s["timestamp"] == ISO_TIMESTAMP for s in state.telemetry["samples"])
    assert state.telemetry["collected_at"] == ISO_TIMESTAMP
    issues = state.findings["normalizer"]["data_quality"]["issues"]
    assert any("epoch timestamp at samples[" in issue and "converted to ISO-8601" in issue
               for issue in issues)
    if epoch_collected_at:
        assert "epoch timestamp at root.collected_at converted to ISO-8601" in issues


@pytest.mark.parametrize("epoch", [True, False, -1, 123, 4133980800,
                                   float("inf"), float("-inf"), float("nan"), 10**1000])
def test_invalid_epochs_exclude_samples(epoch):
    raw = congestion()
    raw["samples"] = [dict(raw["samples"][0], timestamp=epoch)]
    result = normalize(raw)
    assert result.error is None
    assert result.telemetry["samples"] == []
    assert result.quarantined[0]["reason"] == "invalid timestamp"


def test_epoch_merge_and_inferred_collected_at(tmp_path):
    raw = congestion()
    del raw["collected_at"]
    duplicate = deepcopy(raw["samples"][0])
    duplicate["timestamp"] = 1793437200000
    raw["samples"].append(duplicate)
    state, report = run_report(raw, tmp_path)
    assert report["cause"] == "congestion"
    assert len(state.telemetry["samples"]) == 8
    assert state.telemetry["collected_at"] == ISO_TIMESTAMP
    assert all(s["timestamp"] == ISO_TIMESTAMP for s in state.telemetry["samples"])
