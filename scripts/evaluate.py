#!/usr/bin/env python3
"""Evaluate every labeled scenario offline and save runs/evaluation.md."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from netcoop.cli import _evaluate


def main() -> int:
    return _evaluate(ROOT / 'data' / 'scenarios', no_llm=True,
                     output_path=ROOT / 'runs' / 'evaluation.md')


if __name__ == '__main__':
    raise SystemExit(main())
