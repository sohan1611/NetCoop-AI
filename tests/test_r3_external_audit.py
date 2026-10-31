"""Independent third-audit targeted regression tests for NetCoop AI.
Put this file at tests/test_r3_external_audit.py in the checked-out repo
and copy all R3-*.json files into tests/fixtures/qa/.
Tests intentionally express desired behavior and may FAIL before fixes.
Do not count failures as verified until run against your exact deployed commit.
"""
import asyncio
import json
from pathlib import Path
from datetime import datetime, timezone
from netcoop.agents.normalizer import normalize
from netcoop.agents.correlator import _local_scopes
from netcoop.config import SIGNATURES
from netcoop.cli import _cause, _report, build_orchestrator

FIX = Path(__file__).parent / "fixtures" / "qa"
def obj(stem): return json.loads((FIX / (stem + '.json')).read_text())
def report(stem, tmp_path):
    state=asyncio.run(build_orchestrator(tmp_path, no_llm=True).run(obj(stem)))
    return _report(state),_cause(state,_report(state))

def test_r3_01_239_second_pair_must_share_one_scope():
    raw=obj('R3-01_boundary_split_239s')
    scopes=list(_local_scopes(raw, {'A'}, SIGNATURES['latency_degradation']))
    assert any({s.get('latency_ms'),t.get('jitter_ms')}=={250,55} for scope in scopes for s in scope['samples'] for t in scope['samples'] if s.get('latency_ms') is not None and t.get('jitter_ms') is not None), '239 seconds < 300; bucket boundary must not hide co-occurrence'

def test_r3_01_control_150_seconds_should_pass():
    raw=obj('R3-01_control_join_150s')
    scopes=list(_local_scopes(raw, {'A'}, SIGNATURES['latency_degradation']))
    assert any(len(scope['samples'])==2 for scope in scopes)

def test_r3_02_no_false_sibling_cooccurrence():
    raw=obj('R3-02_sibling_evidence_join')
    scopes=list(_local_scopes(raw, {'AP-A','AP-B'}, SIGNATURES['congestion']))
    assert not any({'AP-A','AP-B'} <= {r['device_id'] for r in scope['samples']} for scope in scopes), 'Sibling APs share a parent, but have no direct link and no measured hub failure'

def test_r3_06_alias_input_order_does_not_control_freshness():
    a=normalize(obj('R3-06_alias_conflict_original_order'))
    b=normalize(obj('R3-06_alias_conflict_reversed_order'))
    assert a.telemetry['samples']==b.telemetry['samples'], 'Conflicting collected_at and collectedAt are last-write-wins'
    assert any('conflict' in s.lower() or 'ambiguous' in s.lower() for s in a.data_quality.issues+b.data_quality.issues)

def test_r3_07_invalid_collected_at_is_reported():
    res=normalize(obj('R3-07_invalid_collected_at'))
    assert any('collected_at' in x and ('invalid' in x or 'malformed' in x) for x in res.data_quality.issues)

def test_r3_08_conflicting_status_does_not_make_unqualified_high_confidence_hardware(tmp_path):
    rep,cause=report('R3-08_conflicting_device_status',tmp_path)
    assert not (cause=='hardware_failure' and rep.confidence>=.75 and 'conflict' not in ' '.join(rep.limitations).lower()), 'Any high-confidence hardware verdict must disclose contradictory device status'

def test_r3_03_recovery_must_not_appear_unqualified_as_live_critical(tmp_path):
    rep,cause=report('R3-03_resolved_anomaly',tmp_path)
    # Historical diagnosis can be valid; it must not claim an active current fault without recency qualification.
    if rep.status=='diagnosed' and cause!='normal':
        assert any('historical' in x.lower() or 'resolved' in x.lower() or 'latest' in x.lower() for x in rep.limitations), 'An old anomaly with a healthy latest sample needs a temporal qualification'
