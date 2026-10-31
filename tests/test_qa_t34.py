from netcoop.details import _pct


def test_agent_summary_percent_matches_console_rounding():
    # The console shows Math.round(score * 100); summaries must agree (0.925 -> 93%, not 92%).
    assert [_pct(v) for v in (0.925, 0.86, 0.945, 0.0, 1.0)] == ["93%", "86%", "95%", "0%", "100%"]
