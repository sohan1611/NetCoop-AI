import json
from pathlib import Path
import subprocess
import sys

from netcoop.agents.normalizer import normalize


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gen_scenarios.py"
SCENARIO_DIR = ROOT / "data" / "scenarios"
EXPECTED_SCENARIOS = {
    "normal",
    "congestion",
    "weak_wifi",
    "interface_fault",
    "device_overload",
    "dns_failure",
    "dhcp_failure",
    "interop_renamed_fields_units",
    "schema_v0_legacy",
    "missing_sections",
    "malformed_json",
    "conflicting_evidence",
    "insufficient_data",
    "org_normal",
    "org_latency_spike",
    "org_hardware_failure",
}


def _run_generator() -> None:
    subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, check=True)


def _generated_bytes() -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(SCENARIO_DIR.glob("*.json"))
    }


def test_generator_is_byte_deterministic():
    _run_generator()
    first = _generated_bytes()
    _run_generator()
    assert _generated_bytes() == first


def test_labels_cover_every_scenario_file():
    labels = json.loads((SCENARIO_DIR / "labels.json").read_text(encoding="utf-8"))
    scenario_names = {
        path.stem for path in SCENARIO_DIR.glob("*.json") if path.name != "labels.json"
    }
    assert scenario_names == EXPECTED_SCENARIOS
    assert set(labels) == scenario_names


def test_valid_scenarios_parse_and_normalize_without_error():
    for path in sorted(SCENARIO_DIR.glob("*.json")):
        if path.name in {"labels.json", "malformed_json.json"}:
            continue
        raw = path.read_text(encoding="utf-8")
        json.loads(raw)
        result = normalize(raw)
        assert result.error is None, f"{path.name}: {result.error}"
        assert result.telemetry is not None

    malformed = normalize((SCENARIO_DIR / "malformed_json.json").read_text(encoding="utf-8"))
    assert malformed.error is not None
    assert malformed.telemetry is None
