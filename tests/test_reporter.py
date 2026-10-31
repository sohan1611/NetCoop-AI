import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from netcoop.agents import (
    NormalizerAgent, PerformanceAgent, InfrastructureAgent, ConnectivityAgent,
    CorrelatorAgent, ReporterAgent, normalize,
)
from netcoop.config import HYPOTHESES
from netcoop.orchestrator import Orchestrator
from netcoop.playbook import PLAYBOOK, simulate_remediation
from netcoop.protocol import Envelope, MsgType, Report
from netcoop.state import RunState

SCENARIOS = Path(__file__).resolve().parents[1] / 'data' / 'scenarios'


def _agents(tmp_path):
    return {a.name: a for a in (
        NormalizerAgent(), PerformanceAgent(), InfrastructureAgent(),
        ConnectivityAgent(), CorrelatorAgent(), ReporterAgent(tmp_path),
    )}


@pytest.mark.parametrize('scenario', ['congestion', 'dns_failure', 'insufficient_data', 'conflicting_evidence'])
def test_report_from_real_pipeline(scenario, tmp_path):
    raw = (SCENARIOS / f'{scenario}.json').read_text(encoding='utf-8')
    state = asyncio.run(Orchestrator(_agents(tmp_path), runs_dir=tmp_path).run(raw, run_id=scenario))
    assert isinstance(state, RunState)
    report = Report.model_validate(state.findings['reporter']['report'])
    diagnosis = state.findings['correlator']['diagnosis']
    assert report.confidence == diagnosis['confidence']
    assert report.incident_id == state.telemetry['incident_id']
    assert report.evidence_sources == diagnosis['evidence_sources']
    assert set(diagnosis['conflicts']) <= set(report.limitations)
    if scenario in ('insufficient_data', 'conflicting_evidence'):
        assert report.status == 'insufficient_evidence'
        assert 'insufficient' in report.suspected_root_cause.lower()
        assert 'Likely' not in report.suspected_root_cause
        assert report.recommended_actions == PLAYBOOK['insufficient_evidence']['actions']
    else:
        assert report.status == 'diagnosed'
        assert report.recommended_actions == PLAYBOOK[scenario]['actions']
    saved = Report.model_validate_json((tmp_path / scenario / 'report.json').read_text(encoding='utf-8'))
    assert saved == report
    markdown = (tmp_path / scenario / 'report.md').read_text()
    for heading in ('Root cause', 'Confidence', 'Affected devices', 'Evidence', 'Alternatives', 'Actions', 'Limitations'):
        assert f'## {heading}' in markdown
    assert state.findings['reporter']['narrative'] is None


@pytest.mark.parametrize('cause', ['congestion', 'dns_failure', 'interface_fault', 'weak_wifi', 'device_overload'])
def test_remediation_clears_diagnosis_without_mutation(cause, tmp_path):
    normalized = normalize((SCENARIOS / f'{cause}.json').read_text(encoding='utf-8'))
    telemetry = normalized.telemetry
    original = deepcopy(telemetry)
    patched = simulate_remediation(cause, telemetry)
    assert telemetry == original
    assert patched is not telemetry
    assert patched == simulate_remediation(cause, telemetry)
    assert patched != telemetry
    state = asyncio.run(Orchestrator(_agents(tmp_path), runs_dir=tmp_path).run(patched))
    assert state.findings['correlator']['diagnosis']['cause'] == 'normal'


def test_playbook_covers_hypotheses():
    assert set(PLAYBOOK) == {*HYPOTHESES, 'insufficient_evidence'}
    for entry in PLAYBOOK.values():
        assert entry['actions'] and entry['remediation']


@pytest.mark.parametrize('payload', [{}, {'findings': {}, 'telemetry': {}}, {'run_id': '../escape'}])
def test_invalid_report_task_returns_error(payload, tmp_path):
    task = Envelope(type=MsgType.TASK, sender='orchestrator', recipient='reporter', payload=payload)
    output = asyncio.run(ReporterAgent(tmp_path).handle(task))[0]
    assert output.type == MsgType.ERROR
    assert output.payload['code'] == 'REPORTER_ERROR'


def test_limitations_and_alternatives_preserved(tmp_path):
    diagnosis = dict(status='diagnosed', cause='congestion', confidence=.8,
                     confidence_category='high', affected_devices=['r1'],
                     evidence=[dict(metric='queue_drops', value=10, interpretation='Queue drops observed')],
                     alternatives=[dict(cause='interface_fault', confidence=.6)],
                     evidence_sources=['infrastructure'], conflicts=['Agents disagree'])
    task = Envelope(type=MsgType.TASK, sender='orchestrator', recipient='reporter',
                    correlation_id='run-1', payload=dict(
                        run_id='other', telemetry={'incident_id': 'INC-1'},
                        limitations=['performance unavailable'], findings={
                            'correlator': {'diagnosis': diagnosis},
                            'normalizer': {'data_quality': {'score': .9, 'issues': ['Missing sample']}}}))
    output = asyncio.run(ReporterAgent(tmp_path).handle(task))[0]
    report = Report.model_validate(output.payload['report'])
    assert report.run_id == 'run-1'
    assert report.limitations == ['performance unavailable', 'Agents disagree', 'Missing sample']
    assert report.alternative_causes == ['interface fault (confidence 60.0%)']


def test_write_failure_returns_error(tmp_path):
    blocked = tmp_path / 'blocked'
    blocked.write_text('file')
    agents = _agents(blocked)
    raw = (SCENARIOS / 'congestion.json').read_text(encoding='utf-8')
    state = asyncio.run(Orchestrator(agents, runs_dir=tmp_path).run(raw))
    assert any('reporter unavailable' in item for item in state.limitations)
