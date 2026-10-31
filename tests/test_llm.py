"""Optional LLM behavior is exercised without network requests."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from netcoop import llm
from netcoop.agents.normalizer import normalize
from netcoop.cli import build_orchestrator, main
from netcoop.state import RunState


@pytest.fixture(autouse=True)
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, 'load_dotenv', lambda: None)
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setenv('GEMINI_MODEL', 'test-model')
    monkeypatch.setattr(llm, 'CACHE_DIR', tmp_path / '.llm_cache')
    factory = MagicMock()
    factory.return_value.__enter__.return_value.models.generate_content.return_value.text = 'summary'
    monkeypatch.setattr(llm.genai, 'Client', factory)
    return factory


def test_no_key(client, monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY')
    assert llm.generate('prompt') is None
    client.assert_not_called()


def test_cache_hit(client):
    assert llm.generate('prompt') == 'summary'
    options = client.call_args.kwargs['http_options']
    assert options.timeout == 8000
    assert options.retry_options.attempts == 1
    digest = hashlib.sha256(b'test-modelprompt').hexdigest()
    assert json.loads((llm.CACHE_DIR / f'{digest}.json').read_text()) == 'summary'
    client.reset_mock()
    assert llm.generate('prompt') == 'summary'
    client.assert_not_called()


def test_cache_separates_model_and_prompt(client, monkeypatch):
    assert llm.generate('prompt') == 'summary'
    assert llm.generate('other prompt') == 'summary'
    monkeypatch.setenv('GEMINI_MODEL', 'other-model')
    assert llm.generate('prompt') == 'summary'
    assert client.call_count == 3


@pytest.mark.parametrize('stage', ['client', 'request', 'cache'])
def test_exception_returns_none(client, stage):
    if stage == 'client':
        client.side_effect = RuntimeError('failed')
    elif stage == 'request':
        client.return_value.__enter__.return_value.models.generate_content.side_effect = TimeoutError()
    else:
        llm.CACHE_DIR.write_text('not a directory')
    assert llm.generate('prompt') is None


@pytest.mark.parametrize('response', [None, '', 'not json', '[]', '```bad```'])
def test_malformed_mapping(monkeypatch, response):
    monkeypatch.setattr(llm, 'generate', lambda prompt: response)
    assert llm.map_unknown_fields(['delay']) is None


def test_mapping_parsed_defensively(monkeypatch):
    response = {
        'delay': {'target': 'latency_ms', 'confidence': .9},
        'low': {'target': 'latency_ms', 'confidence': .69},
        'invalid_target': {'target': 'made_up', 'confidence': .99},
        'string': {'target': 'cpu_pct', 'confidence': '0.9'},
        'boolean': {'target': 'cpu_pct', 'confidence': True},
        'nan': {'target': 'cpu_pct', 'confidence': float('nan')},
        'range': {'target': 'cpu_pct', 'confidence': 1.1},
        'shape': [],
        'unrequested': {'target': 'cpu_pct', 'confidence': .9},
    }
    monkeypatch.setattr(llm, 'generate', lambda prompt: '```json\n' + json.dumps(response) + '\n```')
    assert llm.map_unknown_fields([name for name in response if name != 'unrequested']) == {
        'delay': {'target': 'latency_ms', 'confidence': .9},
        'low': {'target': 'latency_ms', 'confidence': .69},
    }


@pytest.mark.parametrize('section,target', [('devices', 'cpu_pct'), ('samples', 'latency_ms')])
@pytest.mark.parametrize('confidence,accepted', [(.7, True), (.95, True), (.699, False)])
def test_mapping_threshold(monkeypatch, section, target, confidence, accepted):
    monkeypatch.setattr(llm, 'map_unknown_fields', lambda fields: {
        'mystery': {'target': target, 'confidence': confidence}})
    raw = {section: [{'device_id': 'r1', 'mystery': 42}]}
    original = deepcopy(raw)
    result = normalize(raw, use_llm=True)
    assert result.error is None
    assert raw == original
    assert (target in result.telemetry[section][0]) == accepted
    assert any('llm_mapped' in issue and f'confidence={confidence:g}' in issue
               for issue in result.data_quality.issues) == accepted
    assert any('mystery dropped' in issue for issue in result.data_quality.issues) != accepted


def test_metrics_mapping(monkeypatch):
    monkeypatch.setattr(llm, 'map_unknown_fields', lambda fields: {
        'processor_load': {'target': 'cpu_pct', 'confidence': .9},
        'round_trip': {'target': 'latency_ms', 'confidence': .8}})
    result = normalize({'device_id': 'r1', 'timestamp': '2026-10-31T00:00:00Z',
                        'metrics': {'processor_load': 80, 'round_trip': 25},
                        'status': 'Healthy'}, use_llm=True)
    assert result.telemetry['devices'][0]['cpu_pct'] == 80
    assert result.telemetry['samples'][0]['latency_ms'] == 25
    assert sum('llm_mapped' in issue for issue in result.data_quality.issues) == 2


@pytest.mark.parametrize('target,confidence', [('made_up', .99), ('latency_ms', .99),
                                              ('cpu_pct', True), ('cpu_pct', float('nan'))])
def test_invalid_mapping_dropped(monkeypatch, target, confidence):
    monkeypatch.setattr(llm, 'map_unknown_fields', lambda fields: {
        'mystery': {'target': target, 'confidence': confidence}})
    result = normalize({'devices': [{'device_id': 'r1', 'mystery': 42}]}, use_llm=True)
    assert result.telemetry['devices'] == [{'device_id': 'r1'}]
    assert not any('llm_mapped' in issue for issue in result.data_quality.issues)


def test_canonical_value_and_alias_not_overwritten(monkeypatch):
    monkeypatch.setattr(llm, 'map_unknown_fields', lambda fields: {
        'mystery': {'target': 'cpu_pct', 'confidence': .99}})
    for key in ('cpu_pct', 'cpu'):
        result = normalize({'devices': [{'device_id': 'r1', key: 20, 'mystery': 99}]}, use_llm=True)
        assert result.telemetry['devices'][0]['cpu_pct'] == 20
        assert not any('llm_mapped' in issue for issue in result.data_quality.issues)


def test_offline_and_known_fields_never_call_llm(monkeypatch):
    mapping = MagicMock()
    monkeypatch.setattr(llm, 'map_unknown_fields', mapping)
    normalize({'devices': [{'device_id': 'r1', 'mystery': 42}]})
    normalize({'devices': [{'device_id': 'r1', 'cpu': 42}]}, use_llm=True)
    normalize({'devices': [{'device_id': 'r1', 'nested': {'mystery': 42}}],
               'services': [{'name': 'dns', 'mystery': 42}]}, use_llm=True)
    mapping.assert_not_called()


@pytest.mark.parametrize('narrative', ['A short diagnosis summary.', None])
def test_reporter_narrative_preserves_diagnosis(monkeypatch, tmp_path, narrative):
    generate = MagicMock(return_value=narrative)
    monkeypatch.setattr(llm, 'generate', generate)
    raw = (Path('data/scenarios') / 'congestion.json').read_text(encoding='utf-8')
    runner = build_orchestrator(tmp_path, no_llm=False)
    state = asyncio.run(runner.run(raw))
    assert isinstance(state, RunState)
    payload = state.findings['reporter']
    diagnosis = state.findings['correlator']['diagnosis']
    assert payload['narrative'] == narrative
    assert payload['report']['confidence'] == diagnosis['confidence']
    assert 'congestion' in payload['report']['suspected_root_cause']
    assert diagnosis['cause'] == 'congestion'
    assert runner.agents['reporter'].narrative == narrative
    markdown = (tmp_path / state.run_id / 'report.md').read_text(encoding='utf-8')
    assert ('## Narrative' in markdown) == bool(narrative)
    if narrative:
        assert narrative in markdown
    generate.assert_called_once()


@pytest.mark.parametrize('offline', [False, True])
def test_cli_wires_llm_flag(monkeypatch, tmp_path, capsys, offline):
    generate = MagicMock(return_value=None)
    monkeypatch.setattr(llm, 'generate', generate)
    argv = ['diagnose', 'data/scenarios/congestion.json', '--runs-dir', str(tmp_path)]
    if offline:
        argv.append('--no-llm')
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'diagnosed'
    assert generate.call_count == (0 if offline else 1)
