"""T36: independent faults remain visible without being called overruled."""

import asyncio
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess

import pytest

from netcoop.agents.correlator import CorrelatorAgent, _RunContext, _score
from netcoop.cli import build_orchestrator
from netcoop.config import ALTERNATIVE_CONFIDENCE_WINDOW
from netcoop.details import build_details
from tests.test_qa_t29 import render_js


def telemetry():
    return {
        "schema_version": "1",
        "collected_at": "2026-10-31T09:01:00Z",
        "devices": [
            {"device_id": "R1", "type": "router", "status": "up", "queue_drops": 300},
            {"device_id": "AP2", "type": "ap", "status": "up"},
        ],
        "samples": [
            {"device_id": "R1", "timestamp": "2026-10-31T09:00:00Z",
             "link_utilization_pct": 96, "latency_ms": 150},
            {"device_id": "AP2", "timestamp": "2026-10-31T09:00:00Z",
             "rssi_dbm": -86, "retransmission_pct": 22, "packet_loss_pct": 6},
        ],
    }


def run(raw, tmp_path):
    state = asyncio.run(build_orchestrator(tmp_path, no_llm=True).run(raw))
    return state, state.findings["correlator"]["diagnosis"], state.findings["reporter"]["report"]


def test_independent_fault_survives_to_report_and_details(tmp_path):
    state, diagnosis, report = run(telemetry(), tmp_path)
    assert diagnosis["cause"] == "congestion"
    assert diagnosis["confidence"] == pytest.approx(0.644, abs=0.001)
    assert diagnosis["affected_devices"] == ["R1"]
    alternative = next(a for a in diagnosis["alternatives"] if a["cause"] == "weak_wifi")
    assert alternative["independent"] is True
    assert alternative["devices"] == ["AP2"]
    assert "overruled_by" not in alternative
    assert (f"weak wifi (confidence {alternative['confidence']:.1%})"
            " \u2014 separate issue on AP2") in report["alternative_causes"]
    assert (f"independent issue: weak_wifi on AP2 ({alternative['confidence']:.0%}) "
            "is not explained by congestion on R1; investigate separately") in report["limitations"]
    assert not any(note.startswith(("selected by", "Conflict resolved by domain rule"))
                   for note in report["limitations"])
    detail = next(a for a in build_details(state, [])["alternatives"] if a["cause"] == "weak_wifi")
    assert detail["independent"] is True and detail["devices"] == ["AP2"]
    assert "overruled_by" not in detail


@pytest.mark.parametrize("location", ["linked", "reverse_linked", "same_device"])
def test_related_faults_keep_overruled_behavior(location, tmp_path):
    raw = telemetry()
    if location == "same_device":
        raw["samples"][0].update({k: v for k, v in raw["samples"].pop().items()
                                   if k not in {"device_id", "timestamp"}})
        raw["devices"].pop()
    else:
        src, dst = ("R1", "AP2") if location == "linked" else ("AP2", "R1")
        raw["topology"] = {"nodes": [{"id": "R1"}, {"id": "AP2"}],
                           "links": [{"src": src, "dst": dst}]}
    _, diagnosis, report = run(raw, tmp_path)
    assert diagnosis["cause"] == "congestion"
    alternative = next(a for a in diagnosis["alternatives"] if a["cause"] == "weak_wifi")
    assert alternative["overruled_by"] == "utilization plus queue drops resolve the conflict as congestion"
    assert "independent" not in alternative and "devices" not in alternative
    assert any(note.startswith("selected by domain rule") for note in report["limitations"])
    assert any(note.startswith("Conflict resolved by domain rule") for note in report["limitations"])
    assert not any(note.startswith("independent issue:") for note in report["limitations"])


@pytest.mark.parametrize("score, visible", [(0.5, True), (0.499, False), (1.0, True)])
def test_independent_confidence_window(score, visible):
    # Exercise display thresholds without changing the diagnostic scoring rules.
    context = _RunContext(run_id="t36", requester="orchestrator", telemetry=telemetry(),
                          findings={}, limitations=[], round=0)
    result = _score(context)
    wifi = next(c for c in result.ordered if c.cause == "weak_wifi")
    wifi.confidence = score
    winner = replace(result.winner, confidence=0.9 if score < 0.6 else 0.6)
    result.winner = winner
    result.ordered = [winner, *result.ordered[1:]]
    assert abs(winner.confidence - score) > ALTERNATIVE_CONFIDENCE_WINDOW
    diagnosis = CorrelatorAgent()._final_finding(context, result).payload["diagnosis"]
    alternatives = [a for a in diagnosis["alternatives"] if a["cause"] == "weak_wifi"]
    assert bool(alternatives) is visible
    if visible:
        assert alternatives[0]["independent"] is True
        assert alternatives[0]["devices"] == ["AP2"]
        assert "overruled_by" not in alternatives[0]


def test_independent_hypothesis_renders_separate_issue():
    render_js("renderHypotheses", """
const assert = require('node:assert/strict');
const elements = {};
const el = (tag, text, cls) => ({tag, text, cls, children: [],
    append(...items) {this.children.push(...items);}, replaceChildren() {this.children = [];}});
const $ = (id) => elements[id] ||= el('div');
const state = {report: {cause: 'congestion', alternative_causes: ['weak wifi']},
    details: {alternatives: [{cause: 'weak_wifi', confidence: 0.827, independent: true,
        devices: ['AP2'], overruled_by: 'must not render', rationale: 'must not render'}]}};
const inputRejected = () => false;
const label = (s) => s;
const percent = (s) => s;
const humanText = (s) => s?.replaceAll('_', ' ');
const confidenceBar = () => el('div');
""", """
const card = elements.alts.children[0];
assert.equal(card.children[2].children[0].text, 'Separate Issue: ');
assert.equal(card.children[2].children[1].text, 'on AP2; not explained by the primary diagnosis');
assert.equal(card.cls, 'alternative-card');
""", helpers=("alternativeList",))


@pytest.mark.parametrize("resolution", [None, "utilization plus queue drops resolve the conflict as congestion"])
def test_selection_note_lists_only_related_higher_candidates(resolution):
    context = _RunContext(run_id="t36", requester="orchestrator", telemetry=telemetry(),
                          findings={}, limitations=[], round=0)
    result = _score(context)
    result.resolution = resolution
    if resolution is None:
        result.conflicts = [note for note in result.conflicts
                            if not note.startswith("Conflict resolved by domain rule")]
    latency = next(c for c in result.ordered if c.cause == "latency_degradation")
    latency.confidence = 0.8
    diagnosis = CorrelatorAgent()._final_finding(context, result).payload["diagnosis"]
    notes = diagnosis["conflicts"]
    selected = next(note for note in notes if note.startswith("selected by"))
    assert "latency_degradation=0.800" in selected
    assert "weak_wifi" not in selected
    assert ("domain rule" if resolution else "agent-weighted rank") in selected
    assert any(note.startswith("Conflict resolved by domain rule") for note in notes) is bool(resolution)


def test_alts_console_describes_separate_issue():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js required for console regression")
    source = (Path(__file__).resolve().parents[1] / "api/static/app.js").read_text(encoding="utf-8")

    def body(prefix):
        start = source.index(prefix)
        return source[start:source.index("\n}", start) + 2]

    script = """
const assert = require('node:assert/strict');
const messages = [];
const consoleMessage = (sender, recipient, text) => messages.push(text);
const state = {report: {cause: 'congestion', alternative_causes: ['weak wifi']},
    details: {alternatives: [{cause: 'weak_wifi', confidence: 0.827, independent: true,
        devices: ['AP2'], overruled_by: 'must not render'}]}};
const label = (s) => s;
const percent = (s) => s;
const humanText = (s) => s;
""" + body("function alternativeList(") + body("async function executeCommand(") + """
executeCommand('alts').then(() => {
    assert.match(messages.at(-1), /separate issue on AP2/);
    assert.doesNotMatch(messages.at(-1), /must not render|overruled/i);
});
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
