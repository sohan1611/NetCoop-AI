"""Robustness stress test: organizer seeds, malformed/perturbed inputs, jittered scenarios, per-agent failure injection.
Run: python scripts/stress_test.py  (no assertions; read the output)."""
import asyncio, json, sys, random, tempfile, traceback, collections
sys.path.insert(0, '.')
from netcoop.cli import build_orchestrator, _report, _cause
from data.generator.organizer_generator import generate_telemetry
TMP = tempfile.mkdtemp()
def run(raw, fail=None):
    try:
        res = asyncio.run(build_orchestrator(TMP, fail_agents=fail, no_llm=True).run(raw if isinstance(raw, str) else json.dumps(raw)))
        rep = _report(res)
        if not isinstance(res, dict):  # T24/D7: domain-overruled higher raw alternatives must name the rule
            d = res.findings['correlator']['diagnosis']
            if any(a['confidence'] >= d['confidence'] and not a.get('overruled_by')
                   for a in d['alternatives']):
                return 'CRASH:INVARIANT unexplained alternative >= confidence', rep
        return _cause(res, rep), rep
    except Exception as e:
        return 'CRASH:' + type(e).__name__ + ':' + str(e)[:120], None
exp = {'normal': 'normal', 'latency_spike': 'latency_degradation', 'hardware_failure': 'hardware_failure'}
print('== organizer single records, 60 seeds each')
for ft, want in exp.items():
    c = collections.Counter(run(generate_telemetry(ft, seed=s))[0] for s in range(60))
    print(ft, dict(c))
print('== organizer list: 6 healthy + 1 hw failure')
recs = [generate_telemetry('normal', seed=s) for s in range(6)]
for i, r in enumerate(recs): r['device_id'] = f'SW_CAMPUS_{i}'
bad = generate_telemetry('hardware_failure', seed=99); bad['device_id'] = 'SW_CAMPUS_BAD'
g, rep = run(recs + [bad]); print(g, rep and rep.affected_devices, rep and rep.confidence)
print('== organizer list: 6 healthy + 1 latency spike')
sp = generate_telemetry('latency_spike', seed=7); sp['device_id'] = 'SW_CAMPUS_SPIKE'
g, rep = run(recs + [sp]); print(g, rep and rep.affected_devices, rep and rep.confidence)
print('== perturbations')
base = generate_telemetry('hardware_failure', seed=3)
def mut(f):
    d = json.loads(json.dumps(base)); f(d); return d
cases = {
 'empty_obj': {}, 'empty_list': [], 'null': None, 'string': 'hello', 'number': 5,
 'not_json': '{bad json', 'list_of_null': [None, None],
 'metrics_null': mut(lambda d: d.update(metrics=None)),
 'metrics_list': mut(lambda d: d.update(metrics=[1, 2])),
 'str_numbers': mut(lambda d: d['metrics'].update({k: str(v) for k, v in d['metrics'].items()})),
 'nan_value': '{"timestamp":"2026-10-31T09:00:00Z","device_id":"SW1","metrics":{"latency_ms":NaN,"cpu_utilization_pct":99.9},"status":"Critical"}',
 'negative': mut(lambda d: d['metrics'].update(latency_ms=-5, packet_loss_pct=-1)),
 'pct_over_100': mut(lambda d: d['metrics'].update(packet_loss_pct=250)),
 'no_device_id': mut(lambda d: d.pop('device_id')),
 'no_timestamp': mut(lambda d: d.pop('timestamp')),
 'bad_timestamp': mut(lambda d: d.update(timestamp='yesterday')),
 'status_lower': mut(lambda d: d.update(status='critical')),
 'status_unknown': mut(lambda d: d.update(status='Purple')),
 'no_status': mut(lambda d: d.pop('status')),
 'camel_fields': mut(lambda d: d.update(metrics={'CpuUtilization': 99.9, 'LatencyMs': 700, 'PacketLoss': 20, 'QueueDrops': 9000, 'InterfaceErrors': 300, 'LinkUtilization': 100})),
 'latency_seconds': mut(lambda d: d['metrics'].update(latency_ms=None) or d['metrics'].pop('latency_ms') or d['metrics'].update(latency_s=0.8)),
 'only_cpu': {'timestamp': '2026-10-31T09:00:00Z', 'device_id': 'SW1', 'metrics': {'cpu_utilization_pct': 99.9}, 'status': 'Critical'},
 'bool_value': mut(lambda d: d['metrics'].update(latency_ms=True)),
 'huge': mut(lambda d: d['metrics'].update(latency_ms=1e308)),
 'nested_extra': mut(lambda d: d.update(extra={'a': [1, {'b': 2}]})),
 'dup_records': [base, base],
 'wrapped': {'records': [base]},
 'canonical_empty': {'schema_version': '1.0'},
 'schema_v9': {'schema_version': '9.0', 'devices': []},
}
for k, v in cases.items():
    g, rep = run(v)
    print(f'{k:16} -> {g} | conf={rep and rep.confidence} | lim={rep and rep.limitations[:2]}')
print('== jittered canonical scenarios (values x0.85..1.15), 10 each')
labels = json.load(open('data/scenarios/labels.json'))
def jitter(o, rng):
    if isinstance(o, dict): return {k: (jitter(v, rng) if k not in ('timestamp','collected_at') else v) for k, v in o.items()}
    if isinstance(o, list): return [jitter(x, rng) for x in o]
    if isinstance(o, float) or (isinstance(o, int) and not isinstance(o, bool) and o > 3): return type(o)(o * rng.uniform(0.85, 1.15))
    return o
for name, want in labels.items():
    txt = open(f'data/scenarios/{name}.json', encoding='utf-8').read()
    try: obj = json.loads(txt)
    except Exception: continue
    ok = collections.Counter()
    for s in range(10):
        ok[run(jitter(obj, random.Random(s)))[0]] += 1
    print(f'{name:30} want={want:22} {dict(ok)}')
print('== fail each agent on congestion')
cong = open('data/scenarios/congestion.json').read()
for a in ['normalizer', 'performance', 'infrastructure', 'connectivity', 'correlator', 'reporter']:
    g, rep = run(cong, {a}); print(a, '->', g, rep and rep.confidence, rep and rep.limitations[:2])
