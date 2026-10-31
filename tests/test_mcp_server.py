import asyncio
import json

import pytest

pytest.importorskip('mcp')
import mcp_server  # noqa: E402


def test_tools_listed():
    names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert names == {'list_scenarios', 'get_telemetry', 'diagnose'}


def test_helpers_and_diagnose():
    assert any(s['name'] == 'congestion' for s in mcp_server.list_scenarios())
    assert json.loads(mcp_server.get_telemetry('congestion'))
    with pytest.raises(ValueError):
        mcp_server.get_telemetry('../pyproject')
    out = asyncio.run(mcp_server.diagnose(scenario='congestion'))
    assert out['cause'] == 'congestion'
    bad = asyncio.run(mcp_server.diagnose(telemetry='{bad'))
    assert bad['status'] == 'error'
