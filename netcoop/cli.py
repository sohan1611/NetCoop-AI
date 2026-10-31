"""Offline CLI for NetCoop."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any

from netcoop.agents.normalizer import NormalizerAgent
from netcoop.agents.performance import PerformanceAgent
from netcoop.agents.infrastructure import InfrastructureAgent
from netcoop.agents.connectivity import ConnectivityAgent
from netcoop.agents.correlator import CorrelatorAgent
from netcoop.agents.reporter import ReporterAgent, _markdown
from netcoop.orchestrator import Orchestrator
from netcoop.playbook import simulate_remediation
from netcoop.protocol import Report
from netcoop.state import RunState


def build_orchestrator(runs_dir: str | Path = 'runs',
                       fail_agents: set[str] | None = None,
                       no_llm: bool = True) -> Orchestrator:
    """Shared real-agent factory with optional Gemini assistance."""
    agents = [NormalizerAgent(use_llm=not no_llm), PerformanceAgent(), InfrastructureAgent(),
              ConnectivityAgent(), CorrelatorAgent(), ReporterAgent(runs_dir, use_llm=not no_llm)]
    return Orchestrator({agent.name: agent for agent in agents},
                        runs_dir=runs_dir, fail_agents=fail_agents)


def _report(result: RunState | dict[str, Any]) -> Report:
    if isinstance(result, RunState):
        return Report.model_validate(result.findings['reporter']['report'])
    reason = str(result['error'])
    return Report(incident_id='unknown', status='error', suspected_root_cause=reason,
                  confidence=0, confidence_category='insufficient', affected_devices=[],
                  supporting_evidence=[], alternative_causes=[], recommended_actions=[],
                  evidence_sources=[], limitations=result.get('limitations', []) or [reason],
                  run_id=result['run_id'])


def _cause(result: RunState | dict[str, Any], report: Report) -> str:
    if report.status in ('error', 'insufficient_evidence'):
        return report.status
    assert isinstance(result, RunState)
    return result.findings['correlator']['diagnosis']['cause']


def _print_report(report: Report, markdown: bool = False) -> int:
    print(_markdown(report) if markdown else report.model_dump_json(indent=2))
    print(report.run_id, file=sys.stderr)
    return 2 if report.status == 'error' else 0


def _evaluate(directory: Path, no_llm: bool = True,
              output_path: Path | None = None) -> int:
    labels = json.loads((directory / 'labels.json').read_text(encoding='utf-8'))
    lines = ['| scenario | expected | got | confidence | pass |',
             '| --- | --- | --- | --- | --- |']
    passed = 0
    with TemporaryDirectory(prefix='netcoop-evaluate-') as runs_dir:
        for scenario, expected in labels.items():
            raw = (directory / f'{scenario}.json').read_text(encoding='utf-8')
            result = asyncio.run(build_orchestrator(runs_dir, no_llm=no_llm).run(raw))
            report = _report(result)
            got = _cause(result, report)
            matched = got == expected
            passed += matched
            lines.append(f'| {scenario} | {expected} | {got} | {report.confidence:.3f} | {matched} |')
    lines.extend(['', f'{passed}/{len(labels)} pass'])
    table = '\n'.join(lines) + '\n'
    print(table, end='')
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(table, encoding='utf-8')
    return 0 if labels and passed * 4 >= len(labels) * 3 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='netcoop')
    commands = parser.add_subparsers(dest='command', required=True)
    diagnose = commands.add_parser('diagnose')
    diagnose.add_argument('file', type=Path)
    diagnose.add_argument('--fail-agent', action='append', default=[])
    diagnose.add_argument('--no-llm', action='store_true')
    diagnose.add_argument('--md', action='store_true')
    diagnose.add_argument('--runs-dir', type=Path, default=Path('runs'))
    evaluate = commands.add_parser('evaluate')
    evaluate.add_argument('--dir', type=Path, default=Path('data/scenarios'))
    evaluate.add_argument('--no-llm', action='store_true')
    for name in ('resume', 'remediate'):
        command = commands.add_parser(name)
        command.add_argument('run_id')
        command.add_argument('--runs-dir', type=Path, default=Path('runs'))
        command.add_argument('--no-llm', action='store_true')
        if name == 'remediate':
            command.add_argument('--approve', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'remediate' and not args.approve:
        print('Human approval is required; pass --approve to simulate remediation.', file=sys.stderr)
        return 1
    try:
        if args.command == 'evaluate':
            return _evaluate(args.dir, no_llm=args.no_llm)
        if args.command == 'diagnose':
            raw = args.file.read_text(encoding='utf-8')
            runner = build_orchestrator(args.runs_dir, set(args.fail_agent), no_llm=args.no_llm)
            return _print_report(_report(asyncio.run(runner.run(raw))), args.md)
        runner = build_orchestrator(args.runs_dir, no_llm=args.no_llm)
        if args.command == 'resume':
            return _print_report(_report(asyncio.run(runner.resume(args.run_id))))
        state = RunState.load(args.run_id, args.runs_dir)
        before_report = Report.model_validate_json(
            (args.runs_dir / args.run_id / 'report.json').read_text(encoding='utf-8'))
        before = _cause(state, before_report)
        if state.telemetry is None:
            raise ValueError('Run has no normalized telemetry to remediate')
        result = asyncio.run(runner.run(simulate_remediation(before, state.telemetry)))
        report = _report(result)
        after = _cause(result, report)
        print(json.dumps({'before': before, 'after': after,
                          'verified': after == 'normal', 'run_id': report.run_id}))
        print(report.run_id, file=sys.stderr)
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f'netcoop: {exc}', file=sys.stderr)
        return 2
