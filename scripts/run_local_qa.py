#!/usr/bin/env python3
"""Read-only local adversarial smoke test of a checked-out NetCoop AI repository.

Run from repository root after `pip install -e '.[dev]'`:
  PYTHONPATH=. python /path/to/netcoop_qa/run_local_qa.py

No deployed URL traffic. No production infrastructure is modified.
"""
from __future__ import annotations

import asyncio
import csv
import json
import sys
import tempfile
from pathlib import Path

QA_ROOT = Path(__file__).resolve().parent
FILES = sorted((QA_ROOT.parent / 'tests' / 'fixtures' / 'qa').glob('*.json'))

try:
    from netcoop.agents.normalizer import normalize
    from netcoop.cli import _cause, _report, build_orchestrator
except ImportError as exc:
    sys.exit(f'Cannot import local NetCoop app: {exc}. Run this from NetCoop repository root after pip install -e ".[dev]".')


def fmt_issues(issues):
    return ' | '.join(str(item) for item in issues[:10]) if issues else ''


async def evaluate_all():
    results = []
    with tempfile.TemporaryDirectory(prefix='netcoop-qa-') as tmp:
        for filepath in FILES:
            raw = filepath.read_text(encoding='utf-8')
            normalized = normalize(raw)
            telemetry = normalized.telemetry or {}
            result = {
                'fixture': filepath.name,
                'normalizer_error': normalized.error or '',
                'quality': normalized.data_quality.score,
                'quality_issues': fmt_issues(normalized.data_quality.issues),
                'normalized_devices': len(telemetry.get('devices', [])),
                'normalized_samples': len(telemetry.get('samples', [])),
                'normalized_first_sample': json.dumps((telemetry.get('samples') or [{}])[0], ensure_ascii=False)[:500],
                'cause': '', 'status': '', 'confidence': '', 'affected_devices': '',
                'report_limitations': '', 'run_error': '',
            }
            try:
                state = await build_orchestrator(Path(tmp), no_llm=True).run(raw)
                rep = _report(state)
                result.update({
                    'cause': _cause(state, rep),
                    'status': rep.status,
                    'confidence': round(rep.confidence, 5),
                    'affected_devices': ','.join(rep.affected_devices),
                    'report_limitations': fmt_issues(rep.limitations),
                })
            except Exception as exc:
                result['run_error'] = f'{type(exc).__name__}: {exc}'
            results.append(result)
            print(f"{result['fixture']:<49} quality={result['quality']:<5} samples={result['normalized_samples']:<5} cause={result['cause']:<24} status={result['status']:<23} confidence={str(result['confidence']):<8} error={result['normalizer_error'] or result['run_error']}")
    return results


if __name__ == '__main__':
    rows = asyncio.run(evaluate_all())
    output = Path(tempfile.gettempdir()) / 'netcoop_local_qa_results.csv'
    with output.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print('\nSaved:', output)
    print('Interpret outcomes against report, not strict hard-coded labels; designed to reveal misattribution, validation gaps and UI-invisible warnings.')
