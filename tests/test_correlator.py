import asyncio
import json
from pathlib import Path

import pytest

from netcoop.agents.base import Agent
from netcoop.agents.connectivity import ConnectivityAgent
from netcoop.agents.correlator import CorrelatorAgent
from netcoop.agents.infrastructure import InfrastructureAgent
from netcoop.agents.normalizer import NormalizerAgent, normalize
from netcoop.agents.performance import PerformanceAgent
from netcoop.orchestrator import Orchestrator
from netcoop.protocol import Envelope, Finding, MsgType
from netcoop.state import RunState


SCENARIO_DIR = Path(__file__).resolve().parents[1] / "data" / "scenarios"
LABELS = json.loads((SCENARIO_DIR / "labels.json").read_text(encoding="utf-8"))
DIAGNOSIS_SCENARIOS = [
    "congestion",
    "weak_wifi",
    "interface_fault",
    "device_overload",
    "dns_failure",
    "dhcp_failure",
    "normal",
    "interop_renamed_fields_units",
    "schema_v0_legacy",
    "org_normal",
    "org_latency_spike",
    "org_hardware_failure",
    "conflicting_evidence",
    "insufficient_data",
]


async def _diagnose(name: str) -> tuple[Envelope, int]:
    normalized = normalize(
        (SCENARIO_DIR / f"{name}.json").read_text(encoding="utf-8")
    )
    assert normalized.error is None
    assert normalized.telemetry is not None
    telemetry = normalized.telemetry
    agents = {
        agent.name: agent
        for agent in (
            PerformanceAgent(),
            InfrastructureAgent(),
            ConnectivityAgent(),
        )
    }
    findings: list[dict] = []
    for agent in agents.values():
        task = Envelope(
            type=MsgType.TASK,
            sender="orchestrator",
            recipient=agent.name,
            correlation_id=name,
            payload={
                "telemetry": telemetry,
                "data_quality": normalized.data_quality.model_dump(mode="json"),
            },
        )
        response = (await agent.handle(task))[0]
        assert response.type == MsgType.FINDING
        findings.append(response.payload)

    correlator = CorrelatorAgent()
    outputs = await correlator.handle(Envelope(
        type=MsgType.TASK,
        sender="orchestrator",
        recipient="correlator",
        correlation_id=name,
        payload={
            "findings": findings,
            "telemetry": telemetry,
            "limitations": [],
            "round": 0,
        },
    ))
    query_count = 0
    while outputs[0].type == MsgType.QUERY:
        query_count += 1
        assert query_count <= 2
        query = outputs[0]
        target = agents[query.payload["target_agent"]]
        response = (await target.handle(query))[0]
        assert response.type in (MsgType.RESPONSE, MsgType.ERROR)
        outputs = await correlator.handle(response)

    assert len(outputs) == 1
    assert outputs[0].type == MsgType.FINDING
    assert outputs[0].sender == "correlator"
    Finding.model_validate(outputs[0].payload)
    return outputs[0], query_count


@pytest.mark.parametrize("scenario", DIAGNOSIS_SCENARIOS)
def test_real_agents_match_scenario_labels(scenario: str):
    output, _ = asyncio.run(_diagnose(scenario))

    assert output.payload["diagnosis"]["cause"] == LABELS[scenario]


@pytest.mark.parametrize(
    "scenario", ["conflicting_evidence", "insufficient_data"]
)
def test_ambiguous_scenarios_end_as_insufficient_evidence(scenario: str):
    output, query_count = asyncio.run(_diagnose(scenario))
    diagnosis = output.payload["diagnosis"]

    assert query_count == 2
    assert diagnosis["status"] == "insufficient_evidence"
    assert diagnosis["cause"] == "insufficient_evidence"
    assert diagnosis["confidence_category"] == "insufficient"


def test_bad_task_returns_error_without_raising():
    task = Envelope(
        type=MsgType.TASK,
        sender="orchestrator",
        recipient="correlator",
        payload={"findings": "not-a-list", "telemetry": {}},
    )

    output = asyncio.run(CorrelatorAgent().handle(task))[0]

    assert output.type == MsgType.ERROR
    assert output.payload["code"] == "CORRELATOR_ERROR"


class _ReporterStub(Agent):
    name = "reporter"

    async def handle(self, envelope: Envelope) -> list[Envelope]:
        return [Envelope(
            type=MsgType.RESPONSE,
            sender=self.name,
            recipient=envelope.sender,
            correlation_id=envelope.msg_id,
            payload={"status": "diagnosed"},
        )]


def test_full_orchestrator_run_uses_real_correlator(tmp_path: Path):
    agents = {
        agent.name: agent
        for agent in (
            NormalizerAgent(),
            PerformanceAgent(),
            InfrastructureAgent(),
            ConnectivityAgent(),
            CorrelatorAgent(),
            _ReporterStub(),
        )
    }
    raw = (SCENARIO_DIR / "congestion.json").read_text(encoding="utf-8")

    state = asyncio.run(Orchestrator(agents, runs_dir=tmp_path).run(raw))

    assert isinstance(state, RunState)
    assert state.findings["correlator"]["diagnosis"]["cause"] == "congestion"
    assert state.findings["correlator"]["diagnosis"]["status"] == "diagnosed"


def test_supporting_evidence_deduplicates_metric_device_value():
    from netcoop.agents.correlator import _RunContext, _score

    telemetry = normalize(
        (SCENARIO_DIR / 'congestion.json').read_text(encoding='utf-8')
    ).telemetry
    evidence = dict(metric='latency_ms', device_id='r1', value=185,
                    interpretation='High latency', timestamp='2026-10-31T09:00:00Z')
    context = _RunContext(
        run_id='dedupe', requester='orchestrator', telemetry=telemetry,
        limitations=[], round=2, findings={
            'performance': {'evidence': [evidence]},
            'connectivity': {'evidence': [
                {**evidence, 'interpretation': 'Path delay', 'timestamp': None},
                {**evidence, 'device_id': 'r2'},
                {**evidence, 'value': 200},
            ]},
        },
    )
    selected, _ = CorrelatorAgent()._supporting_evidence(context, _score(context).winner)
    latency = [item for item in selected if item['metric'] == 'latency_ms']
    assert [(item['device_id'], item['value']) for item in latency] == [
        ('r1', 185), ('r2', 185), ('r1', 200),
    ]
    assert latency[0]['interpretation'] == 'High latency'
