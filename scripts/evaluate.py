#!/usr/bin/env python3
"""Evaluate every labeled scenario offline and save runs/evaluation.md."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from netcoop.cli import _evaluate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-llm', action='store_true')
    parser.add_argument('--min-pass-rate', type=float, default=1.0)
    args = parser.parse_args()
    return _evaluate(ROOT / 'data' / 'scenarios', no_llm=True,
                     output_path=ROOT / 'runs' / 'evaluation.md',
                     min_pass_rate=args.min_pass_rate)


if __name__ == '__main__':
    raise SystemExit(main())
