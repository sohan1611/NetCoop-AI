import asyncio
import json
from pathlib import Path

import pytest

from netcoop.agents.connectivity import ConnectivityAgent, analyze_connectivity
from netcoop.agents.normalizer import normalize
from netcoop.protocol import Envelope, Finding, MsgType


SCENARIO_DIR = Path(__file__).resolve().parents[1] / "data" / "scenarios"


def _scenario(name: str) -> dict:
    raw = json.loads((SCENARIO_DIR / f"{name}.json").read_text(encoding="utf-8"))
    result = normalize(raw)
    assert result.error is None
    assert result.telemetry is not None
    return result.telemetry


def _score(finding: Finding, cause: str) -> float:
    return next(item.score for item in finding.hypotheses if item.cause == cause)


def _evidence_value(finding: Finding, metric: str) -> str:
    return str(next(item.value for item in finding.evidence if item.metric == metric))


def test_inline_topology_finds_common_upstream_blast_radius_and_isolation():
    telemetry = {
        "schema_version": "1.0",
        "incident_id": "INC-INLINE",
        "collected_at": "2026-10-31T09:00:00Z",
        "topology": {
            "nodes": [
                {"id": "CORE", "type": "router", "role": "core"},
                {"id": "DIST", "type": "switch", "role": "distribution"},
                {"id": "AP-1", "type": "ap", "role": "access"},
                {"id": "AP-2", "type": "ap", "role": "access"},
            ],
            "links": [
                {"src": "CORE", "dst": "DIST"},
                {"src": "DIST", "dst": "AP-1"},
                {"src": "DIST", "dst": "AP-2"},
            ],
        },
        "devices": [
            {"device_id": "DIST", "type": "switch", "status": "down"},
            {"device_id": "AP-1", "type": "ap", "status": "degraded"},
            {"device_id": "AP-2", "type": "ap", "status": "up"},
        ],
        "samples": [],
        "services": [],
        "symptoms": [],
    }

    finding = analyze_connectivity(telemetry)

    assert finding.agent == "connectivity"
    assert finding.affected_devices == ["DIST", "AP-1"]
    assert _evidence_value(finding, "common_upstream") == "DIST"
    assert _evidence_value(finding, "blast_radius") == "AP-1, AP-2, DIST"
    assert "AP-1" in _evidence_value(finding, "isolated_segment")
    assert _score(finding, "hardware_failure") == 1.0


@pytest.mark.parametrize(
    ("scenario", "cause", "server_id"),
    [
        ("dns_failure", "dns_failure", "DNS_SRV_01"),
        ("dhcp_failure", "dhcp_failure", "DHCP_SRV_01"),
    ],
)
def test_service_failure_scenarios_point_to_correct_service(
    scenario: str,
    cause: str,
    server_id: str,
):
    finding = analyze_connectivity(_scenario(scenario))

    assert finding.hypotheses[0].cause == cause
    assert server_id in finding.affected_devices
    prefix = cause.removesuffix("_failure")
    service_evidence = [
        item for item in finding.evidence if item.metric.startswith(prefix)
    ]
    assert service_evidence
    assert all(item.device_id == server_id for item in service_evidence)
    assert any(server_id in note and "is down" in note for note in finding.notes)


def test_interface_fault_points_to_device_and_upstream():
    finding = analyze_connectivity(_scenario("interface_fault"))

    assert "DEPT_SW_01" in finding.affected_devices
    assert _score(finding, "interface_fault") == 1.0
    assert _evidence_value(finding, "common_upstream") == "ACADEMIC_DIST_SW_01"
    loss = next(item for item in finding.evidence if item.metric == "packet_loss_pct")
    assert loss.device_id == "DEPT_SW_01"


def test_normal_input_ranks_normal():
    finding = analyze_connectivity(_scenario("normal"))

    assert finding.hypotheses[0].cause == "normal"
    assert finding.affected_devices == []


def test_missing_topology_keeps_service_checks_and_reports_limitation():
    result = normalize(
        (SCENARIO_DIR / "org_normal.json").read_text(encoding="utf-8")
    )
    assert result.telemetry is not None
    telemetry = result.telemetry
    telemetry["services"] = [
        {
            "name": "dns",
            "server_id": "DNS-X",
            "status": "down",
            "success_rate_pct": 0,
            "response_ms": 1500,
        }
    ]

    finding = analyze_connectivity(telemetry, data_quality=result.data_quality)

    assert "no topology supplied" in finding.notes
    assert _score(finding, "dns_failure") == 1.0


def test_task_and_upstream_neighbors_query_envelopes():
    agent = ConnectivityAgent()
    telemetry = _scenario("interface_fault")
    task = Envelope(
        type=MsgType.TASK,
        sender="orchestrator",
        recipient="connectivity",
        payload={"telemetry": telemetry},
    )
    response = asyncio.run(agent.handle(task))[0]
    Finding.model_validate(response.payload)
    assert response.type == MsgType.FINDING
    assert response.correlation_id == task.msg_id

    query = Envelope(
        type=MsgType.QUERY,
        sender="correlator",
        recipient="connectivity",
        payload={
            "action": "upstream_neighbors",
            "params": {"device_id": "ACADEMIC_DIST_SW_01"},
        },
    )
    query_response = asyncio.run(agent.handle(query))[0]

    assert query_response.type == MsgType.RESPONSE
    assert query_response.correlation_id == query.msg_id
    assert query_response.payload["upstream_neighbors"] == ["CORE_RTR_01"]
    assert query_response.payload["downstream_neighbors"] == [
        "DEPT_SW_01",
        "LECTURE_AP_01",
    ]
