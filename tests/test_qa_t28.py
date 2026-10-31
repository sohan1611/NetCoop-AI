"""Regressions for T28: NC-042, NC-043, NC-044 and reopened NC-031."""
import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import server
from netcoop.agents.normalizer import normalize
from netcoop.cli import build_orchestrator
from netcoop.config import INSUFFICIENT_CONFIDENCE_THRESHOLD

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/qa"


def fixture(prefix):
    return json.loads(next(FIXTURES.glob(f"{prefix}_*.json")).read_text(encoding="utf-8"))


def report(raw, tmp_path):
    state = asyncio.run(build_orchestrator(tmp_path, no_llm=True).run(raw))
    return json.loads((tmp_path / state.run_id / "report.json").read_text(encoding="utf-8"))


def test_nc042_healthy_history_preserves_current_diagnosis(tmp_path):
    raw = json.loads((ROOT / "data/scenarios/congestion.json").read_text(encoding="utf-8"))
    plain = normalize(raw)
    history = []
    for hours in range(2, 6):
        for sample in raw["samples"]:
            timestamp = datetime.fromisoformat(sample["timestamp"].replace("Z", "+00:00"))
            history.append(dict(sample, timestamp=(timestamp - timedelta(hours=hours)).isoformat(),
                                latency_ms=8, link_utilization_pct=20, packet_loss_pct=0))
    raw["samples"] = history + raw["samples"]
    result = normalize(raw)
    assert result.data_quality.score == plain.data_quality.score
    assert result.telemetry["samples"] == plain.telemetry["samples"]
    assert len(result.quarantined) == len(history)
    assert result.data_quality.issues == [
        f"{len(history)} samples older than the analysis window (collected_at - 1 h) excluded"
    ]
    diagnosis = report(raw, tmp_path)
    assert diagnosis["cause"] == "congestion"
    assert diagnosis["confidence"] >= 0.9


@pytest.mark.parametrize("prefix", ["02", "03", "05", "07", "11"])
def test_nc042_invalid_and_outside_window_fixtures(prefix, tmp_path):
    raw = fixture(prefix)
    diagnosis = report(raw, tmp_path)
    if prefix == "11":
        assert diagnosis["cause"] != "congestion"
    else:
        assert diagnosis["cause"] == "insufficient_evidence"
    if prefix in {"03", "07"}:
        result = normalize(raw)
        assert result.data_quality.score < INSUFFICIENT_CONFIDENCE_THRESHOLD
        assert "no samples inside the analysis window" in result.data_quality.issues


def test_nc042_future_samples_have_one_counted_issue_and_penalty():
    raw = fixture("00")
    baseline = normalize(raw)
    future = []
    reference = datetime.fromisoformat(raw["collected_at"].replace("Z", "+00:00"))
    for minutes in (6, 7, 8):
        future.append(dict(raw["samples"][0], timestamp=(reference + timedelta(minutes=minutes)).isoformat()))
    raw["samples"].extend(future)
    result = normalize(raw)
    issues = [issue for issue in result.data_quality.issues if "future" in issue]
    assert len(issues) == 1
    assert "3 future samples" in issues[0] and "first example:" in issues[0]
    assert future[0]["timestamp"] in issues[0]
    assert len(result.quarantined) == 3
    assert result.data_quality.score < baseline.data_quality.score
    assert result.telemetry["samples"] == baseline.telemetry["samples"]


def test_nc043_pending_upload_limit_is_per_session(tmp_path):
    app = server.create_app(tmp_path)
    with TestClient(app) as a, TestClient(app) as b:
        for _ in range(5):
            assert a.post("/api/upload", content=b"{}").status_code == 200
        sixth = a.post("/api/upload", content=b"{}")
        assert sixth.status_code == 413
        assert sixth.json()["detail"] == "too many pending uploads"
        assert b.post("/api/upload", content=b"{}").status_code == 200


def test_nc043_run_consumes_upload_and_owner(tmp_path):
    with TestClient(server.create_app(tmp_path)) as client:
        upload_id = client.post("/api/upload", content=b"{}").json()["upload_id"]
        path = tmp_path / "uploads" / f"{upload_id}.json"
        assert path.is_file() and path.with_suffix(".owner").is_file()
        started = client.post("/api/run", json={"upload_id": upload_id})
        assert started.status_code == 200
        assert not path.exists() and not path.with_suffix(".owner").exists()
        assert client.post("/api/run", json={"upload_id": upload_id}).status_code == 404
        assert client.post("/api/upload", content=b"{}").status_code == 200
        client.get(f"/api/runs/{started.json()['run_id']}/report")


@pytest.mark.parametrize("limit", ["files", "bytes"])
def test_nc043_global_safety_caps(tmp_path, monkeypatch, limit):
    monkeypatch.setattr(server, "MAX_UPLOAD_FILES", 1 if limit == "files" else 200)
    monkeypatch.setattr(server, "MAX_UPLOAD_TOTAL_BYTES", 3 if limit == "bytes" else 100_000_000)
    app = server.create_app(tmp_path)
    with TestClient(app) as a, TestClient(app) as b:
        assert a.post("/api/upload", content=b"{}").status_code == 200
        response = b.post("/api/upload", content=b"{}")
        assert response.status_code == 413
        assert response.json()["detail"] == "upload storage limit exceeded"


@pytest.mark.parametrize("prefix,expected", [("01", True), ("02", False), ("11", False)])
def test_nc044_locality_limitations_require_global_signature(prefix, expected, tmp_path):
    limitations = report(fixture(prefix), tmp_path)["limitations"]
    assert any("evidence from unrelated devices/times" in item for item in limitations) is expected
    if expected:
        assert "congestion: evidence from unrelated devices/times" in limitations


def test_nc044_warning_limitations_only_for_relevant_candidates(tmp_path):
    warning = report(fixture("06"), tmp_path / "warning")
    assert any("warning-grade devices: FAULTY" in item for item in warning["limitations"])
    raw = (ROOT / "data/scenarios/congestion.json").read_text(encoding="utf-8")
    congestion = report(raw, tmp_path / "congestion")
    assert not any("warning-grade devices:" in item for item in congestion["limitations"])


def test_nc031_upload_notice_is_below_file_input():
    html = (server.STATIC / "index.html").read_text(encoding="utf-8")
    notice = ('<p class="muted upload-notice">Upload synthetic or anonymised telemetry only. '
              'This is a public demo: uploads are private to your browser session and deleted after 1 hour.</p>')
    assert html.index('type="file"') < html.index(notice) < html.index('id="clear-upload"')
