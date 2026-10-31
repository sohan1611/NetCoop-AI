from netcoop.details import _pct
from netcoop.playbook import simulate_remediation


def test_agent_summary_percent_matches_console_rounding():
    # The console shows Math.round(score * 100); summaries must agree (0.925 -> 93%, not 92%).
    assert [_pct(v) for v in (0.925, 0.86, 0.945, 0.0, 1.0)] == ["93%", "86%", "95%", "0%", "100%"]


def test_remediation_copy_drops_normalizer_bookkeeping():
    # Re-diagnosing the copy must not report "unknown field root.quarantined".
    telemetry = {"devices": [], "samples": [], "quarantined": [{"device_id": "R1"}]}
    patched = simulate_remediation("congestion", telemetry)
    assert "quarantined" not in patched and "quarantined" in telemetry


def test_numeric_string_parsing_is_linear_on_long_whitespace():
    # Audit A7: overlapping \s* around the unit made this quadratic (50 KB of spaces took ~9 s).
    import time
    from netcoop.agents.normalizer import _NUMERIC_STRING
    start = time.perf_counter()
    assert _NUMERIC_STRING.match("1" + " " * 1_000_000 + "!") is None
    assert time.perf_counter() - start < 0.5
