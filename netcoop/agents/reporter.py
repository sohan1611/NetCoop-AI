"""Validated JSON and Markdown reports from correlator diagnoses."""
from pathlib import Path
from typing import Any
from netcoop.agents.base import Agent
from netcoop.playbook import PLAYBOOK
from netcoop.protocol import Envelope, MsgType, Report

_CAUSES = {
    'normal': 'Network telemetry is within normal operating limits.',
    'congestion': 'Likely link congestion causing egress queue drops and delay.',
    'weak_wifi': 'Likely weak Wi-Fi signal causing retransmissions and packet loss.',
    'interface_fault': 'Likely interface fault causing errors and packet loss.',
    'device_overload': 'Likely device overload from high CPU or memory utilization.',
    'dns_failure': 'Likely DNS service failure preventing name resolution.',
    'dhcp_failure': 'Likely DHCP service failure preventing address allocation.',
    'latency_degradation': 'Likely network path degradation causing latency and jitter.',
    'hardware_failure': 'Likely hardware failure affecting device availability.',
    'insufficient_evidence': 'Evidence is insufficient to identify a root cause; collect more data.',
}


def _cell(value: Any) -> str:
    return str(value).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')


def _markdown(report: Report) -> str:
    lines = [f'# Incident {report.incident_id}', f'Run: {report.run_id}',
             '', '## Root cause', report.suspected_root_cause or 'Unknown',
             '', '## Confidence', f'{report.confidence:.1%} ({report.confidence_category})',
             '', '## Affected devices', ', '.join(report.affected_devices) or 'None identified',
             '', '## Evidence', '| Metric | Value | Interpretation |', '| --- | --- | --- |']
    lines.extend(f'| {_cell(e.metric)} | {_cell(e.value)} | {_cell(e.interpretation)} |'
                 for e in report.supporting_evidence)
    for heading, items in (('Evidence sources', report.evidence_sources),
                           ('Alternatives', report.alternative_causes),
                           ('Actions', report.recommended_actions),
                           ('Limitations', report.limitations)):
        lines.extend(['', f'## {heading}', ''])
        lines.extend(f'- {item}' for item in items or ['None reported'])
    return '\n'.join(lines) + '\n'


class ReporterAgent(Agent):
    name = 'reporter'
    narrative = None  # Optional narrative hook reserved for T10.

    def __init__(self, runs_dir: str | Path = 'runs') -> None:
        self.runs_dir = Path(runs_dir)

    async def handle(self, envelope: Envelope) -> list[Envelope]:
        try:
            if envelope.type != MsgType.TASK:
                raise ValueError('reporter expects a TASK')
            payload = envelope.payload
            run_id = envelope.correlation_id or payload['run_id']
            if not isinstance(run_id, str) or not run_id or run_id in ('.', '..') or any(c in run_id for c in '/\\:'):
                raise ValueError('run_id must be a single directory name')
            findings = payload['findings']
            diagnosis = findings['correlator']['diagnosis']
            cause = diagnosis['cause']
            if diagnosis['status'] == 'insufficient_evidence':
                cause = 'insufficient_evidence'
            limitations = list(payload.get('limitations', []))
            limitations.extend(diagnosis.get('conflicts', []))
            for finding in findings.values():
                quality = finding.get('data_quality')
                if quality is not None:
                    limitations.extend(quality.get('issues', []))
            report = Report(
                incident_id=payload['telemetry']['incident_id'],
                status=diagnosis['status'], suspected_root_cause=_CAUSES[cause],
                confidence=diagnosis['confidence'],
                confidence_category=diagnosis['confidence_category'],
                affected_devices=diagnosis['affected_devices'],
                supporting_evidence=diagnosis['evidence'],
                alternative_causes=[f"{item['cause'].replace('_', ' ')} (confidence {item['confidence']:.1%})"
                                    for item in diagnosis.get('alternatives', [])],
                recommended_actions=PLAYBOOK[cause]['actions'],
                evidence_sources=diagnosis['evidence_sources'],
                limitations=list(dict.fromkeys(limitations)), run_id=run_id,
            )
            directory = self.runs_dir / run_id
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'report.json').write_text(report.model_dump_json(indent=2) + '\n', encoding='utf-8')
            (directory / 'report.md').write_text(_markdown(report), encoding='utf-8')
            return [Envelope(type=MsgType.FINDING, sender=self.name,
                             recipient=envelope.sender, correlation_id=run_id,
                             payload={'report': report.model_dump(mode='json'), 'narrative': self.narrative})]
        except Exception as exc:
            return [Envelope(type=MsgType.ERROR, sender=self.name,
                             recipient=envelope.sender,
                             correlation_id=envelope.correlation_id or envelope.msg_id,
                             payload={'code': 'REPORTER_ERROR', 'message': str(exc), 'recoverable': True})]
