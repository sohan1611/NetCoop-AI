"""T24 fixture regressions for locality, time joins, normal and transparency."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from netcoop.agents.correlator import _RunContext, _score
from netcoop.agents.normalizer import normalize
from netcoop.cli import build_orchestrator
from netcoop.config import INSUFFICIENT_CONFIDENCE_THRESHOLD


FIXTURES = Path(__file__).parent / "fixtures" / "qa"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def run_report(raw, tmp_path):
    state = asyncio.run(build_orchestrator(tmp_path, no_llm=True).run(raw))
    report = json.loads((tmp_path / state.run_id / "report.json").read_text(encoding="utf-8"))
    return state.findings["correlator"], report


@pytest.mark.parametrize("connected", [False, True])
def test_nc001_signature_requires_local_support(connected, tmp_path):
    raw = fixture("01_disconnected_signals.json")
    if connected:
        raw["topology"] = {
            "nodes": [{"id": device} for device in ("AP-A", "SW-B", "HOST-C")],
            "links": [{"src": "SW-B", "dst": device} for device in ("AP-A", "HOST-C")],
        }
    if connected:
        raw["samples"].append(dict(raw["samples"][1], device_id="REMOTE", latency_ms=800))
    finding, report = run_report(raw, tmp_path)
    congestion = next(item["score"] for item in finding["hypotheses"] if item["cause"] == "congestion")
    if connected:
        assert report["cause"] == "congestion"
        assert congestion >= 0.75
        assert "REMOTE" not in report["affected_devices"]
        assert all(item.get("device_id") != "REMOTE" for item in report["supporting_evidence"])
        assert not any("evidence from unrelated devices/times" in item for item in report["limitations"])
    else:
        assert congestion < INSUFFICIENT_CONFIDENCE_THRESHOLD
        assert not (report["cause"] == "congestion" and report["confidence"] >= 0.75)
        assert any("evidence from unrelated devices/times" in item for item in report["limitations"])


@pytest.mark.parametrize("minutes", [2, 5, 6, 20, 24 * 60])
def test_nc003_signature_requires_same_time_bucket(minutes, tmp_path):
    raw = fixture("11_signals_24h_apart.json")
    finding, report = run_report(raw, tmp_path / "original")
    assert report["cause"] != "congestion"
    # Exercise the join independently of T23: retain both samples inside its
    # one-hour freshness window, with complementary utilization/latency peaks.
    reference = datetime(2026, 10, 31, 9, tzinfo=timezone.utc)
    raw["samples"][0]["timestamp"] = (reference - timedelta(minutes=minutes)).isoformat()
    telemetry = normalize(raw).telemetry
    # Bypass only the freshness filter for the 24-hour correlator-only case.
    if minutes == 24 * 60:
        telemetry["samples"] = deepcopy(raw["samples"])
    context = _RunContext("time", "orchestrator", {}, telemetry, [], 2)
    result = _score(context)
    congestion = next(item for item in result.ordered if item.cause == "congestion")
    if minutes <= 5:
        assert congestion.observations["link_utilization_pct"].grade == 1
        assert congestion.observations["latency_ms"].grade == 1
    else:
        assert not (congestion.observations["link_utilization_pct"].grade == 1
                    and congestion.observations["latency_ms"].grade == 1)
        assert result.winner.cause != "congestion"


@pytest.mark.parametrize("warning", [False, True, "boundary"])
def test_nc006_normal_uses_worst_device(warning, tmp_path):
    raw = fixture("06_warn_only_in_healthy_fleet.json" if warning else "00_control_normal.json")
    if warning == "boundary":
        raw["devices"][-1]["interface_errors"] = 1
        raw["samples"][-1].update(latency_ms=100, packet_loss_pct=1)
    finding, report = run_report(raw, tmp_path)
    normal = next(item["score"] for item in finding["hypotheses"] if item["cause"] == "normal")
    if warning:
        assert normal < 0.75
        if warning is True:
            assert report["cause"] != "normal"  # The UI calls diagnosed normal Healthy.
        assert report["confidence"] < 0.75 or report["cause"] != "normal"
        assert any("warning-grade" in item and "FAULTY" in item for item in report["limitations"])
    else:
        assert report["cause"] == "normal"
        assert not any("critical" in item["interpretation"].lower()
                       for item in report["supporting_evidence"])


def test_nc012_domain_overruled_alternatives_remain_visible(tmp_path):
    raw = fixture("00_control_normal.json")
    raw["devices"][0].update(status="down", cpu_pct=99, mem_pct=99)
    finding, report = run_report(raw, tmp_path)
    diagnosis = finding["diagnosis"]
    assert report["cause"] == "hardware_failure"
    higher = [item for item in diagnosis["alternatives"]
              if item["confidence"] >= diagnosis["confidence"]]
    assert any(item["cause"] == "device_overload" for item in higher)
    assert all(item["overruled_by"] == "a device reported down" for item in higher)
    raw_scores = {item["cause"]: item["score"] for item in finding["hypotheses"]}
    assert all(item["confidence"] == raw_scores[item["cause"]] for item in higher)
