import asyncio

import pytest
from netcoop.agents.normalizer import NormalizerAgent, normalize
from netcoop.protocol import Envelope, MsgType


def test_aliases_and_units_normalize_to_canonical():
    result = normalize(
        {
            "schema_version": "1.0",
            "incident_id": "INC-1",
            "collected_at": "2026-10-31T10:00:00Z",
            "devices": [
                {
                    "device_id": "SW-1",
                    "status": "up",
                    "cpu_utilization_pct": 70,
                    "memory_ratio": 0.5,
                }
            ],
            "samples": [
                {
                    "device_id": "SW-1",
                    "timestamp": "2026-10-31T10:00:00Z",
                    "latency_s": 0.125,
                    "throughput_kbps": 25_000,
                    "packet_loss_ratio": 0.02,
                }
            ],
        }
    )

    assert result.error is None
    assert result.telemetry is not None
    assert result.telemetry["schema_version"] == "1.0"
    assert result.telemetry["devices"][0]["cpu_pct"] == 70
    assert result.telemetry["devices"][0]["mem_pct"] == 50
    sample = result.telemetry["samples"][0]
    assert sample["latency_ms"] == 125
    assert sample["throughput_mbps"] == 25
    assert sample["packet_loss_pct"] == 2


def test_organizer_single_and_list_formats():
    first = {
        "timestamp": "2026-10-31T10:00:00Z",
        "device_id": "SW_CAMPUS_1",
        "metrics": {
            "cpu_utilization_pct": 40,
            "latency_ms": 20,
            "queue_drops": 2,
        },
        "status": "Healthy",
    }
    single = normalize(first)
    assert single.telemetry is not None
    assert single.telemetry["devices"] == [
        {
            "device_id": "SW_CAMPUS_1",
            "status": "up",
            "cpu_pct": 40,
            "queue_drops": 2,
        }
    ]
    assert single.telemetry["samples"][0]["latency_ms"] == 20
    assert single.telemetry["topology"] == {"nodes": [], "links": []}

    second = {**first, "device_id": "SW_CAMPUS_2", "status": "Critical"}
    many = normalize([first, second])
    assert many.telemetry is not None
    assert len(many.telemetry["devices"]) == 2
    assert many.telemetry["devices"][1]["status"] == "degraded"
    assert any("severity critical" in issue for issue in many.data_quality.issues)


def test_v0_legacy_rows_are_upgraded():
    result = normalize(
        {
            "schema_version": "0",
            "incident_id": "INC-OLD",
            "readings": [
                {
                    "id": "R-1",
                    "timestamp": "2026-10-31T10:00:00Z",
                    "cpu": 75,
                    "lat": 12,
                    "loss": 1,
                    "status": "up",
                }
            ],
        }
    )

    assert result.error is None
    assert result.telemetry is not None
    assert result.telemetry["schema_version"] == "1.0"
    assert result.telemetry["devices"][0]["device_id"] == "R-1"
    assert result.telemetry["devices"][0]["cpu_pct"] == 75
    assert result.telemetry["samples"][0]["latency_ms"] == 12
    assert result.telemetry["samples"][0]["packet_loss_pct"] == 1


def test_malformed_json_returns_error_result_and_envelope():
    result = normalize('{"bad":')
    assert result.telemetry is None
    assert result.error is not None
    assert "invalid JSON" in result.error

    task = Envelope(
        type=MsgType.TASK,
        sender="orchestrator",
        recipient="normalizer",
        correlation_id="INC-1",
        payload={"raw": '{"bad":'},
    )
    responses = asyncio.run(NormalizerAgent().handle(task))
    assert len(responses) == 1
    assert responses[0].type == MsgType.ERROR
    assert "invalid JSON" in responses[0].payload["message"]


def test_duplicate_out_of_range_and_stale_samples_lower_score():
    result = normalize(
        {
            "schema_version": "1.0",
            "incident_id": "INC-1",
            "collected_at": "2026-10-31T12:30:00Z",
            "topology": {"nodes": [], "links": []},
            "devices": [{"device_id": "SW-1", "status": "up"}],
            "samples": [
                {
                    "device_id": "SW-1",
                    "timestamp": "2026-10-31T10:00:00Z",
                    "latency_ms": -1,
                    "packet_loss_pct": 120,
                },
                {
                    "device_id": "SW-1",
                    "timestamp": "2026-10-31T10:00:00Z",
                    "latency_ms": 5,
                },
                {
                    "device_id": "SW-1",
                    "timestamp": "2026-10-31T12:30:00Z",
                    "latency_ms": 5,
                },
            ],
            "services": [],
        }
    )

    assert result.error is None
    assert result.data_quality.score < 1
    issues = " ".join(result.data_quality.issues)
    assert "duplicate sample" in issues
    assert "out-of-range" in issues
    assert "stale timestamp" in issues
    assert result.telemetry is not None
    assert len(result.telemetry["samples"]) == 2


def test_missing_optional_sections_use_empty_defaults():
    result = normalize(
        {
            "schema_version": "1.0",
            "incident_id": "INC-1",
            "collected_at": "2026-10-31T10:00:00Z",
            "devices": [],
        }
    )

    assert result.error is None
    assert result.telemetry is not None
    assert result.telemetry["topology"] == {"nodes": [], "links": []}
    assert result.telemetry["samples"] == []
    assert result.telemetry["services"] == []
    assert any("missing section topology" in issue for issue in result.data_quality.issues)


def test_handle_returns_finding_with_separate_telemetry_key():
    task = Envelope(
        type=MsgType.TASK,
        sender="orchestrator",
        recipient="normalizer",
        payload={"telemetry": []},
    )
    response = asyncio.run(NormalizerAgent().handle(task))[0]

    assert response.type == MsgType.FINDING
    assert response.payload["agent"] == "normalizer"
    assert response.payload["notes"] == []
    assert response.payload["data_quality"] is not None
    assert response.payload["telemetry"]["schema_version"] == "1.0"


def test_null_optional_metric_is_not_a_quality_issue():
    from netcoop.agents.normalizer import normalize
    import json
    raw = json.loads(open('data/scenarios/congestion.json', encoding='utf-8').read())
    result = normalize(raw)
    assert not any('expected a number' in i for i in result.data_quality.issues)
    assert result.data_quality.score > 0.8


def test_numeric_strings_are_coerced_not_dropped():
    result = normalize({'device_id': 'SW1', 'timestamp': '2026-10-31T09:00:00Z',
                        'metrics': {'cpu_utilization_pct': '99.9%', 'latency_ms': ' 845.7 ms'}})
    device = result.telemetry['devices'][0]
    assert device['cpu_pct'] == 99.9
    assert result.telemetry['samples'][0]['latency_ms'] == 845.7
    assert not any('wrong type' in issue for issue in result.data_quality.issues)
    assert any('coerced numeric string' in issue for issue in result.data_quality.issues)
    assert result.data_quality.score == 1.0


def test_organizer_record_without_device_id_gets_placeholder():
    result = normalize({'timestamp': '2026-10-31T09:00:00Z', 'metrics': {'cpu_utilization_pct': 12.0}})
    assert result.telemetry['devices'][0]['device_id'] == 'UNKNOWN_DEVICE_0'
    assert result.telemetry['devices'][0]['cpu_pct'] == 12.0
    assert any('device_id; using UNKNOWN_DEVICE_0' in issue for issue in result.data_quality.issues)


def test_clean_organizer_record_has_full_quality_and_high_confidence():
    import asyncio
    from tempfile import TemporaryDirectory
    from netcoop.cli import build_orchestrator
    from pathlib import Path
    raw = (Path(__file__).resolve().parents[1] / 'data' / 'scenarios' / 'org_normal.json').read_text(encoding='utf-8')
    with TemporaryDirectory() as runs:
        state = asyncio.run(build_orchestrator(runs).run(raw))
    assert state.findings['normalizer']['data_quality']['score'] == 1.0
    assert any('missing section topology' in item for item in state.findings['normalizer']['data_quality']['issues'])
    diagnosis = state.findings['correlator']['diagnosis']
    assert diagnosis['cause'] == 'normal' and diagnosis['confidence'] >= 0.7


def test_camel_case_metric_names_are_mapped_without_llm():
    result = normalize({'device_id': 'SW1', 'timestamp': '2026-10-31T09:00:00Z', 'metrics': {
        'CpuUtilization': 99.9, 'LatencyMs': 700, 'PacketLoss': 20, 'QueueDrops': 9000,
        'InterfaceErrors': 300, 'LinkUtilization': 100}})
    device, sample = result.telemetry['devices'][0], result.telemetry['samples'][0]
    assert (device['cpu_pct'], device['queue_drops'], device['interface_errors']) == (99.9, 9000, 300)
    assert (sample['latency_ms'], sample['packet_loss_pct'], sample['link_utilization_pct']) == (700, 20, 100)
    assert not any('unknown field' in issue for issue in result.data_quality.issues)


@pytest.mark.parametrize('wrapper', ['records', 'data', 'telemetry', 'items'])
def test_wrapped_record_list_is_unwrapped(wrapper):
    record = {'device_id': 'SW1', 'timestamp': '2026-10-31T09:00:00Z', 'metrics': {'cpu_utilization_pct': 50}}
    result = normalize({wrapper: [record]})
    assert result.telemetry['devices'][0]['cpu_pct'] == 50
    assert not any('unknown field' in issue for issue in result.data_quality.issues)


@pytest.mark.parametrize('field,value', [('latency_ms', 70000), ('queue_drops', 2e9), ('packet_loss_pct', 150)])
def test_impossible_values_are_flagged(field, value):
    result = normalize({'device_id': 'SW1', 'timestamp': '2026-10-31T09:00:00Z', 'metrics': {field: value}})
    assert any('out-of-range' in issue for issue in result.data_quality.issues)
