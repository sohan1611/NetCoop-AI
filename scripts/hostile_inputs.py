"""Hostile-input probe: deep nesting, huge ints, inf, 20k devices, bad UTF-8, injection-like IDs. Prints time + outcome per case."""
import asyncio, sys, tempfile, time
sys.path.insert(0,'.')
from netcoop.cli import build_orchestrator, _report, _cause
T=tempfile.mkdtemp()
def run(name, raw):
    t=time.time()
    try:
        r=asyncio.run(build_orchestrator(T).run(raw)); rep=_report(r); out=_cause(r,rep)+' '+str(rep.limitations[:1])[:150]
    except BaseException as e: out='CRASH '+type(e).__name__+': '+str(e)[:150]
    print(f'{name:14} {time.time()-t:6.2f}s {out}')
run('deep_nest', '['*100000 + ']'*100000)
run('deep_dict', '{"a":'*50000 + '1' + '}'*50000)
run('big_int', '{"device_id":"X","metrics":{"latency_ms":' + '9'*5000 + '},"status":"Critical"}')
run('inf', '{"device_id":"X","metrics":{"latency_ms":1e999,"cpu_utilization_pct":99},"status":"Critical"}')
recs = ','.join('{"device_id":"SW%d","metrics":{"latency_ms":10,"cpu_utilization_pct":20},"status":"Healthy"}' % i for i in range(20000))
run('20k_devices', '[' + recs + ']')
# 20k healthy devices with a full metric set (sparse 2-metric records above stay insufficient_evidence, by design)
import json
from data.generator.organizer_generator import generate_telemetry
full = []
for i in range(20000):
    rec = generate_telemetry('normal', seed=i % 50); rec['device_id'] = f'SW{i}'; full.append(rec)
run('20k_full', json.dumps(full))
run('bad_utf8', b'\xff\xfe{"a":1}')
run('id_inject', '{"device_id":"<img src=x onerror=alert(1)>","metrics":{"cpu_utilization_pct":99.9,"interface_errors":300},"status":"Critical"}')
run('incident_path', '{"schema_version":"1.0","incident_id":"../../evil","devices":[{"device_id":"A","status":"down"}],"samples":[{"device_id":"A","timestamp":"2026-10-31T09:00:00Z","latency_ms":900}]}')
