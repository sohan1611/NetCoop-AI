import asyncio

from netcoop.agents.infrastructure import InfrastructureAgent, analyze_infrastructure
from netcoop.agents.performance import PerformanceAgent, analyze_performance
from netcoop.protocol import Envelope, Finding, MsgType


def _top(finding: Finding) -> str:
    return finding.hypotheses[0].cause


def test_congestion_is_top_performance_hypothesis():
    finding = analyze_performance(
        {
            "schema_version": "1.0",
            "incident_id": "INC-CONGESTION",
            "collected_at": "2026-10-31T10:05:00Z",
            "topology": {"nodes": [], "links": []},
            "devices": [{"device_id": "DIST-1", "type": "switch", "status": "up"}],
            "samples": [
                {
                    "device_id": "DIST-1",
                    "timestamp": "2026-10-31T10:00:00Z",
                    "latency_ms": 25,
                    "jitter_ms": 4,
                    "packet_loss_pct": 0.1,
                    "throughput_mbps": 700,
                    "bandwidth_mbps": 1000,
                    "link_utilization_pct": 55,
                },
                {
                    "device_id": "DIST-1",
                    "timestamp": "2026-10-31T10:05:00Z",
                    "latency_ms": 190,
                    "jitter_ms": 55,
                    "packet_loss_pct": 6,
                    "throughput_mbps": 350,
                    "bandwidth_mbps": 1000,
                    "link_utilization_pct": 96,
                },
            ],
            "services": [],
            "symptoms": ["slow network"],
        }
    )

    assert _top(finding) == "congestion"
    assert finding.affected_devices == ["DIST-1"]
    assert any(item.metric == "link_utilization_pct" for item in finding.evidence)


def test_weak_wifi_is_top_performance_hypothesis():
    finding = analyze_performance(
        {
            "schema_version": "1.0",
            "incident_id": "INC-WIFI",
            "collected_at": "2026-10-31T10:05:00Z",
            "topology": {"nodes": [], "links": []},
            "devices": [{"device_id": "AP-7", "type": "ap", "status": "up"}],
            "samples": [
                {
                    "device_id": "AP-7",
                    "timestamp": "2026-10-31T10:00:00Z",
                    "packet_loss_pct": 0.2,
                    "rssi_dbm": -58,
                    "retransmission_pct": 0.5,
                },
                {
                    "device_id": "AP-7",
                    "timestamp": "2026-10-31T10:05:00Z",
                    "packet_loss_pct": 3,
                    "rssi_dbm": -82,
                    "retransmission_pct": 9,
                },
            ],
            "services": [],
            "symptoms": [],
        }
    )

    assert _top(finding) == "weak_wifi"
    assert finding.affected_devices == ["AP-7"]
    assert any(item.metric == "rssi_dbm" for item in finding.evidence)


def test_interface_fault_is_top_infrastructure_hypothesis():
    finding = analyze_infrastructure(
        {
            "schema_version": "1.0",
            "incident_id": "INC-IFACE",
            "collected_at": "2026-10-31T10:05:00Z",
            "topology": {"nodes": [], "links": []},
            "devices": [
                {
                    "device_id": "SW-3",
                    "type": "switch",
                    "status": "degraded",
                    "cpu_pct": 30,
                    "mem_pct": 45,
                    "interface_errors": 28,
                    "queue_drops": 0,
                }
            ],
            "samples": [],
            "services": [],
            "symptoms": [],
        }
    )

    assert _top(finding) == "interface_fault"
    assert finding.affected_devices == ["SW-3"]
    assert any(item.metric == "interface_errors" for item in finding.evidence)


def test_device_overload_is_top_infrastructure_hypothesis():
    finding = analyze_infrastructure(
        {
            "schema_version": "1.0",
            "incident_id": "INC-LOAD",
            "collected_at": "2026-10-31T10:05:00Z",
            "topology": {"nodes": [], "links": []},
            "devices": [
                {
                    "device_id": "RTR-2",
                    "type": "router",
                    "status": "up",
                    "cpu_pct": 99,
                    "mem_pct": 97,
                    "interface_errors": 0,
                    "queue_drops": 0,
                }
            ],
            "samples": [],
            "services": [],
            "symptoms": [],
        }
    )

    assert _top(finding) == "device_overload"
    assert finding.affected_devices == ["RTR-2"]


def test_normal_inputs_rank_normal_and_have_no_affected_devices():
    telemetry = {
        "schema_version": "1.0",
        "incident_id": "INC-NORMAL",
        "collected_at": "2026-10-31T10:05:00Z",
        "topology": {"nodes": [], "links": []},
        "devices": [
            {
                "device_id": "SW-1",
                "type": "switch",
                "status": "up",
                "cpu_pct": 25,
                "mem_pct": 40,
                "interface_errors": 0,
                "queue_drops": 0,
            }
        ],
        "samples": [
            {
                "device_id": "SW-1",
                "timestamp": "2026-10-31T10:05:00Z",
                "latency_ms": 12,
                "jitter_ms": 2,
                "packet_loss_pct": 0,
                "throughput_mbps": 100,
                "bandwidth_mbps": 1000,
                "link_utilization_pct": 20,
                "rssi_dbm": -50,
                "retransmission_pct": 0.1,
            }
        ],
        "services": [],
        "symptoms": [],
    }

    performance = analyze_performance(telemetry)
    infrastructure = analyze_infrastructure(telemetry)
    assert _top(performance) == "normal"
    assert _top(infrastructure) == "normal"
    assert performance.affected_devices == []
    assert infrastructure.affected_devices == []


def test_performance_query_returns_detailed_finding_response():
    agent = PerformanceAgent()
    telemetry = {
        "samples": [
            {
                "device_id": "AP-1",
                "timestamp": "2026-10-31T10:00:00Z",
                "latency_ms": 20,
                "rssi_dbm": -55,
            },
            {
                "device_id": "AP-1",
                "timestamp": "2026-10-31T10:05:00Z",
                "latency_ms": 150,
                "rssi_dbm": -72,
            },
        ]
    }
    task = Envelope(
        type=MsgType.TASK,
        sender="orchestrator",
        recipient="performance",
        payload={"telemetry": telemetry},
    )
    asyncio.run(agent.handle(task))
    query = Envelope(
        type=MsgType.QUERY,
        sender="correlator",
        recipient="performance",
        payload={"action": "widen_window", "params": {"window": 15}},
    )

    response = asyncio.run(agent.handle(query))[0]
    finding = Finding.model_validate(response.payload)
    assert response.type == MsgType.RESPONSE
    assert response.correlation_id == query.msg_id
    assert finding.agent == "performance"
    assert len(finding.evidence) >= 2
    assert any("widen_window" in note for note in finding.notes)


def test_infrastructure_device_detail_query_filters_the_device():
    agent = InfrastructureAgent()
    telemetry = {
        "devices": [
            {"device_id": "SW-1", "status": "up", "cpu_pct": 10, "mem_pct": 20},
            {
                "device_id": "SW-2",
                "status": "up",
                "cpu_pct": 98,
                "mem_pct": 96,
                "interface_errors": 0,
                "queue_drops": 0,
            },
        ]
    }
    task = Envelope(
        type=MsgType.TASK,
        sender="orchestrator",
        recipient="infrastructure",
        payload={"telemetry": telemetry},
    )
    task_response = asyncio.run(agent.handle(task))[0]
    assert task_response.correlation_id == task.msg_id
    query = Envelope(
        type=MsgType.QUERY,
        sender="correlator",
        recipient="infrastructure",
        payload={"action": "device_detail", "params": {"device_id": "SW-2"}},
    )

    response = asyncio.run(agent.handle(query))[0]
    finding = Finding.model_validate(response.payload)
    assert response.type == MsgType.RESPONSE
    assert response.correlation_id == query.msg_id
    assert _top(finding) == "device_overload"
    assert finding.affected_devices == ["SW-2"]
    assert all(item.device_id == "SW-2" for item in finding.evidence)


def test_missing_telemetry_is_not_an_exception_and_is_not_confidently_normal():
    task = Envelope(
        type=MsgType.TASK,
        sender="orchestrator",
        recipient="performance",
        payload={},
    )

    response = asyncio.run(PerformanceAgent().handle(task))[0]
    finding = Finding.model_validate(response.payload)
    assert response.type == MsgType.FINDING
    assert finding.hypotheses[0].score <= 0.2
    assert any("did not contain" in note for note in finding.notes)
