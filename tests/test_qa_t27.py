"""Regressions for T27 follow-up QA findings NC-034 through NC-040."""
import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from api import server
from netcoop.agents.normalizer import normalize
from netcoop.cli import build_orchestrator
from netcoop.config import ALTERNATIVE_CONFIDENCE_WINDOW

ROOT = Path(__file__).resolve().parents[1]


def run_report(raw, tmp_path):
    state = asyncio.run(build_orchestrator(tmp_path, no_llm=True).run(raw))
    report = json.loads((tmp_path / state.run_id / "report.json").read_text(encoding="utf-8"))
    return state.findings["correlator"], report


def test_nc034_unresolved_conflict_preserves_raw_leaders(tmp_path):
    raw = (ROOT / "data/scenarios/conflicting_evidence.json").read_text(encoding="utf-8")
    finding, report = run_report(raw, tmp_path)
    diagnosis = finding["diagnosis"]
    assert diagnosis["status"] == "insufficient_evidence"
    assert diagnosis["confidence"] <= 0.299
    scores = {item["cause"]: item["score"] for item in finding["hypotheses"]}
    winner_score = finding["hypotheses"][0]["score"]
    expected = {cause for cause, score in scores.items()
                if abs(winner_score - score) <= ALTERNATIVE_CONFIDENCE_WINDOW}
    alternatives = {item["cause"]: item for item in diagnosis["alternatives"]}
    assert {"weak_wifi", "device_overload"} <= expected
    assert expected <= alternatives.keys()
    for cause in expected:
        assert alternatives[cause]["confidence"] == scores[cause]
        assert alternatives[cause]["overruled_by"] == "unresolved agent conflict"
        assert (f"{cause.replace('_', ' ')} (confidence {scores[cause]:.1%})"
                " \u2014 overruled by unresolved agent conflict") in report["alternative_causes"]


def test_nc035_domain_override_reaches_report(tmp_path):
    raw = json.loads((ROOT / "tests/fixtures/qa/00_control_normal.json").read_text(encoding="utf-8"))
    raw["devices"][0].update(status="down", cpu_pct=99, mem_pct=99)
    finding, report = run_report(raw, tmp_path)
    for item in finding["diagnosis"]["alternatives"]:
        expected = f"{item['cause'].replace('_', ' ')} (confidence {item['confidence']:.1%})"
        if item.get("overruled_by"):
            expected += f" \u2014 overruled by {item['overruled_by']}"
        assert expected in report["alternative_causes"]
    assert any("overruled by a device reported down" in item for item in report["alternative_causes"])


def test_nc036_duplicate_device_status_is_reported_and_neutralized(tmp_path):
    raw = json.loads((ROOT / "tests/fixtures/qa/00_control_normal.json").read_text(encoding="utf-8"))
    device = raw["devices"][0]
    device["status"] = "degraded"
    raw["devices"].append(dict(device, status="down"))
    normalized = normalize(raw)
    rows = [row for row in normalized.telemetry["devices"] if row["device_id"] == device["device_id"]]
    assert [row["status"] for row in rows] == [None, None]
    issue = f"conflicting status for duplicate device_id={device['device_id']}: degraded vs down"
    _, report = run_report(raw, tmp_path)
    assert issue in report["limitations"]
    raw["devices"][-1]["status"] = "degraded"
    _, report = run_report(raw, tmp_path / "same")
    assert not any("conflicting status for duplicate" in item for item in report["limitations"])


def test_nc037_fatal_only_run_returns_sanitized_error_report(tmp_path, monkeypatch):
    def crash(*args, **kwargs):
        raise RuntimeError("private fatal details")

    monkeypatch.setattr(server, "build_orchestrator", crash)
    with TestClient(server.create_app(tmp_path)) as client:
        run_id = client.post("/api/run", json={"scenario": "congestion"}).json()["run_id"]
        response = client.get(f"/api/runs/{run_id}/report")
        assert response.status_code == 200
        report = response.json()
        assert report["status"] == report["cause"] == "error"
        assert report["limitations"] == ["run failed before producing a report"]
        assert report["run_id"] == run_id
        assert "private fatal details" not in response.text
        assert (tmp_path / run_id / "fatal.txt").is_file()
        assert not (tmp_path / run_id / "state.json").exists()
        assert not (tmp_path / run_id / "report.json").exists()


def test_nc040_locality_limitations_identify_the_candidate(tmp_path):
    raw = (ROOT / "tests/fixtures/qa/01_disconnected_signals.json").read_text(encoding="utf-8")
    _, report = run_report(raw, tmp_path)
    limitations = [item for item in report["limitations"] if "evidence from unrelated devices/times" in item]
    assert limitations
    assert "congestion: evidence from unrelated devices/times" in limitations
    assert all(": evidence from unrelated devices/times" in item for item in limitations)
