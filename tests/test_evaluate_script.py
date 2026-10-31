"""Offline evaluation entrypoint and accuracy gate acceptance checks."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from netcoop.cli import _evaluate

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / 'data' / 'scenarios'


def test_evaluate_script_runs_and_passes(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'evaluate.py')],
                            cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith('| scenario | expected | got | confidence | pass |')
    labels = json.loads((SCENARIOS / 'labels.json').read_text(encoding='utf-8'))
    rows = result.stdout.splitlines()[2:-2]
    assert len(rows) == len(labels)
    for row, (scenario, expected) in zip(rows, labels.items()):
        cells = [cell.strip() for cell in row.strip('|').split('|')]
        assert cells[:2] == [scenario, expected]
        assert 0 <= float(cells[3]) <= 1
        assert cells[4] == str(cells[1] == cells[2])
    passed, total = map(int, result.stdout.splitlines()[-1].split()[0].split('/'))
    assert total == len(labels)
    assert passed == total
    assert (ROOT / 'runs' / 'evaluation.md').read_text(encoding='utf-8') == result.stdout


@pytest.mark.parametrize('matches, expected_code', [(2, 1), (3, 1), (4, 0)])
def test_evaluation_accuracy_gate(matches, expected_code, tmp_path, capsys):
    labels = {f'case_{i}': 'normal' if i < matches else 'congestion' for i in range(4)}
    raw = (SCENARIOS / 'normal.json').read_text(encoding='utf-8')
    for name in labels:
        (tmp_path / f'{name}.json').write_text(raw, encoding='utf-8')
    (tmp_path / 'labels.json').write_text(json.dumps(labels), encoding='utf-8')
    output = tmp_path / 'runs' / 'evaluation.md'
    assert _evaluate(tmp_path, no_llm=True, output_path=output) == expected_code
    table = capsys.readouterr().out
    assert table.endswith(f'{matches}/4 pass\n')
    assert output.read_text(encoding='utf-8') == table
