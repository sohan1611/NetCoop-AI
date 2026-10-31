"""MCP server exposing NetCoop diagnosis as tools (stdio). Run: python mcp_server.py"""
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp.server.mcpserver import MCPServer

from netcoop.cli import _cause, _report, build_orchestrator

SCENARIOS = Path(__file__).resolve().parent / 'data' / 'scenarios'
_ID = re.compile(r'^[A-Za-z0-9_.-]+$')
mcp = MCPServer('netcoop-ai')


def _path(name: str) -> Path:
    if not _ID.match(name) or name.startswith('.') or name == 'labels':
        raise ValueError('invalid scenario name')
    path = SCENARIOS / f'{name}.json'
    if not path.is_file():
        raise ValueError(f'unknown scenario: {name}')
    return path


@mcp.tool()
def list_scenarios() -> list[dict]:
    """List bundled telemetry scenarios with their expected root cause."""
    labels = json.loads((SCENARIOS / 'labels.json').read_text(encoding='utf-8'))
    return [{'name': name, 'expected': expected} for name, expected in labels.items()]


@mcp.tool()
def get_telemetry(scenario: str) -> str:
    """Return the raw telemetry JSON text of a bundled scenario."""
    return _path(scenario).read_text(encoding='utf-8')


@mcp.tool()
async def diagnose(scenario: str | None = None, telemetry: str | None = None,
                   fail_agent: str | None = None) -> dict:
    """Run the multi-agent diagnosis (offline, no LLM) on a scenario name or raw telemetry text.
    Optionally inject a failing agent (e.g. infrastructure) to see graceful degradation."""
    if scenario:
        raw = _path(scenario).read_text(encoding='utf-8')
    elif telemetry:
        raw = telemetry
    else:
        raise ValueError('provide scenario or telemetry')
    with TemporaryDirectory() as runs:
        result = await build_orchestrator(runs, {fail_agent} if fail_agent else set(),
                                          no_llm=True).run(raw)
        report = _report(result)
        data = report.model_dump(mode='json')
        data['cause'] = _cause(result, report)
        return data


if __name__ == '__main__':
    mcp.run()
