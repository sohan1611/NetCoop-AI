"""Acceptance checks for the real offline CLI pipeline."""
import json

import pytest
from pathlib import Path
import subprocess
import sys

from netcoop.cli import main
from netcoop.protocol import Report

SCENARIOS = Path(__file__).resolve().parents[1] / 'data' / 'scenarios'


def diagnose(tmp_path, capsys, *options, scenario='congestion'):
    code = main(['diagnose', str(SCENARIOS / f'{scenario}.json'), '--no-llm',
                 '--runs-dir', str(tmp_path), *options])
    output = capsys.readouterr()
    report = Report.model_validate_json(output.out)
    assert output.err.strip() == report.run_id
    return code, report


def test_module_entrypoint(tmp_path):
    result = subprocess.run(
        [sys.executable, '-m', 'netcoop', 'diagnose', str(SCENARIOS / 'congestion.json'),
         '--no-llm', '--runs-dir', str(tmp_path)], capture_output=True, text=True,
        check=False)
    assert result.returncode == 0, result.stderr
    report = Report.model_validate_json(result.stdout)
    assert 'congestion' in report.suspected_root_cause
    assert result.stderr.strip() == report.run_id


def test_malformed_json(tmp_path, capsys):
    code, report = diagnose(tmp_path, capsys, scenario='malformed_json')
    assert code == 2
    assert report.status == 'error'
    assert report.suspected_root_cause
    assert report.limitations


def test_failure_injection(tmp_path, capsys):
    code, report = diagnose(tmp_path, capsys, '--fail-agent', 'infrastructure',
                            '--fail-agent', 'connectivity')
    assert code == 0
    assert any('infrastructure' in item for item in report.limitations)
    assert any('connectivity' in item for item in report.limitations)


def test_evaluate(capsys):
    assert main(['evaluate', '--dir', str(SCENARIOS), '--no-llm']) == 0
    output = capsys.readouterr().out
    print(output)
    passed, total = map(int, output.strip().splitlines()[-1].split()[0].split('/'))
    assert total == 16
    assert passed >= 12
    assert all(name in output for name in json.loads(
        (SCENARIOS / 'labels.json').read_text(encoding='utf-8')))


def test_resume_finished_run(tmp_path, capsys):
    _, report = diagnose(tmp_path, capsys)
    assert main(['resume', report.run_id, '--runs-dir', str(tmp_path)]) == 0
    assert Report.model_validate_json(capsys.readouterr().out) == report


def test_remediation_requires_approval(capsys):
    assert main(['remediate', 'not-loaded-without-approval']) == 1
    assert 'approval' in capsys.readouterr().err.lower()


def test_remediation_verified(tmp_path, capsys):
    _, report = diagnose(tmp_path, capsys)
    original = (tmp_path / report.run_id / 'state.json').read_text(encoding='utf-8')
    assert main(['remediate', report.run_id, '--approve', '--runs-dir', str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['before'] == 'congestion'
    assert result['after'] == 'normal'
    assert result['verified'] is True
    assert result['run_id'] != report.run_id
    assert (tmp_path / report.run_id / 'state.json').read_text(encoding='utf-8') == original


def test_markdown(tmp_path, capsys):
    assert main(['diagnose', str(SCENARIOS / 'congestion.json'), '--no-llm',
                 '--runs-dir', str(tmp_path), '--md']) == 0
    output = capsys.readouterr().out
    assert output.startswith('# Incident')
    assert 'congestion' in output


def test_correlator_failure_degrades(tmp_path, capsys):
    code, report = diagnose(tmp_path, capsys, '--fail-agent', 'correlator')
    assert code == 0
    assert report.status == 'insufficient_evidence'
    assert any(item.startswith('correlator unavailable') for item in report.limitations)
    assert report.affected_devices and report.supporting_evidence


def test_reporter_failure_degrades(tmp_path, capsys):
    code, report = diagnose(tmp_path, capsys, '--fail-agent', 'reporter')
    assert code == 0
    assert report.status == 'diagnosed'
    assert any(item.startswith('reporter unavailable') for item in report.limitations)


@pytest.mark.parametrize('status', [None, 'Purple'])
def test_org_hardware_failure_without_usable_status(tmp_path, capsys, status):
    record = json.loads((SCENARIOS / 'org_hardware_failure.json').read_text(encoding='utf-8'))
    record.pop('status')
    if status:
        record['status'] = status
    path = tmp_path / 'hw.json'
    path.write_text(json.dumps(record), encoding='utf-8')
    code = main(['diagnose', str(path), '--no-llm', '--runs-dir', str(tmp_path / 'runs')])
    report = Report.model_validate_json(capsys.readouterr().out)
    assert code == 0
    assert 'hardware failure' in report.suspected_root_cause
