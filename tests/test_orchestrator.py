import asyncio
import json
from pathlib import Path

from netcoop.agents.base import Agent
from netcoop.agents.connectivity import ConnectivityAgent
from netcoop.agents.infrastructure import InfrastructureAgent
from netcoop.agents.normalizer import NormalizerAgent, normalize
from netcoop.agents.performance import PerformanceAgent
from netcoop.bus import MessageBus
from netcoop.orchestrator import Orchestrator
from netcoop.protocol import Envelope, MsgType
from netcoop.state import RunState


SCENARIO_DIR = Path(__file__).resolve().parents[1] / "data" / "scenarios"


class StubCorrelator(Agent):
    name = "correlator"

    async def handle(self, envelope: Envelope) -> list[Envelope]:
        return [Envelope(
            type=MsgType.FINDING,
            sender=self.name,
            recipient=envelope.sender,
            correlation_id=envelope.msg_id,
            payload={
                "status": "correlated",
                "finding_count": len(envelope.payload["findings"]),
            },
        )]


class StubReporter(Agent):
    name = "reporter"

    async def handle(self, envelope: Envelope) -> list[Envelope]:
        return [Envelope(
            type=MsgType.RESPONSE,
            sender=self.name,
            recipient=envelope.sender,
            correlation_id=envelope.msg_id,
            payload={
                "status": "diagnosed",
                "limitations": list(envelope.payload["limitations"]),
            },
        )]


class SlowAgent(Agent):
    name = "performance"

    def __init__(self) -> None:
        self.calls = 0

    async def handle(self, envelope: Envelope) -> list[Envelope]:
        self.calls += 1
        await asyncio.sleep(1)
        return []


class CountingAgent(Agent):
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    async def handle(self, envelope: Envelope) -> list[Envelope]:
        self.calls += 1
        return []


class QueryTarget(Agent):
    name = "performance"

    def __init__(self) -> None:
        self.query_calls = 0

    async def handle(self, envelope: Envelope) -> list[Envelope]:
        if envelope.type == MsgType.TASK:
            message_type = MsgType.FINDING
            payload = {
                "agent": self.name,
                "hypotheses": [],
                "affected_devices": [],
                "evidence": [],
                "notes": [],
            }
        else:
            self.query_calls += 1
            message_type = MsgType.RESPONSE
            payload = {"answer": self.query_calls}
        return [Envelope(
            type=message_type,
            sender=self.name,
            recipient=envelope.sender,
            correlation_id=envelope.msg_id,
            payload=payload,
        )]


class LoopingCorrelator(Agent):
    name = "correlator"

    async def handle(self, envelope: Envelope) -> list[Envelope]:
        return [Envelope(
            type=MsgType.QUERY,
            sender=self.name,
            recipient="performance",
            correlation_id=envelope.msg_id,
            payload={
                "target_agent": "performance",
                "action": "widen_window",
                "params": {"minutes": 15},
                "reason": "need more evidence",
            },
        )]


def _agents(**overrides: Agent) -> dict[str, Agent]:
    agents: dict[str, Agent] = {
        "normalizer": NormalizerAgent(),
        "performance": PerformanceAgent(),
        "infrastructure": InfrastructureAgent(),
        "connectivity": ConnectivityAgent(),
        "correlator": StubCorrelator(),
        "reporter": StubReporter(),
    }
    agents.update({agent.name: agent for agent in overrides.values()})
    return agents


def _scenario(name: str) -> str:
    return (SCENARIO_DIR / f"{name}.json").read_text(encoding="utf-8")


def test_full_run_writes_messages_and_state(tmp_path: Path):
    state = asyncio.run(
        Orchestrator(_agents(), runs_dir=tmp_path).run(_scenario("congestion"))
    )

    assert isinstance(state, RunState)
    assert state.stage == "complete"
    assert state.completed_stages == [
        "normalize", "analysis", "correlator", "reporter"
    ]
    assert state.findings["reporter"]["status"] == "diagnosed"
    run_dir = tmp_path / state.run_id
    restored = RunState.load(state.run_id, tmp_path)
    assert restored == state
    messages = [
        json.loads(line)
        for line in (run_dir / "messages.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert messages
    assert all(Envelope.model_validate(message) for message in messages)


def test_failed_agent_degrades_and_run_completes(tmp_path: Path):
    state = asyncio.run(Orchestrator(
        _agents(), runs_dir=tmp_path, fail_agents={"infrastructure"}
    ).run(_scenario("congestion")))

    assert isinstance(state, RunState)
    assert state.stage == "complete"
    assert "infrastructure unavailable: injected failure" in state.limitations
    assert state.findings["reporter"]["limitations"] == state.limitations


def test_slow_agent_times_out_retries_and_degrades(tmp_path: Path):
    slow = SlowAgent()
    state = asyncio.run(Orchestrator(
        _agents(performance=slow),
        runs_dir=tmp_path,
        timeout_s=0.01,
        retries=1,
    ).run(_scenario("congestion")))

    assert isinstance(state, RunState)
    assert state.stage == "complete"
    assert slow.calls == 2
    assert "performance unavailable: timeout after 0.01s" in state.limitations


def test_malformed_json_returns_error_without_exception(tmp_path: Path):
    result = asyncio.run(
        Orchestrator(_agents(), runs_dir=tmp_path).run(_scenario("malformed_json"))
    )

    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert "invalid JSON" in result["error"]
    state = RunState.load(result["run_id"], tmp_path)
    assert state.stage == "error"


def test_invalid_bus_envelope_becomes_error_and_is_published(tmp_path: Path):
    bus = MessageBus("invalid-envelope", tmp_path)
    queue = bus.subscribe()

    responses = asyncio.run(bus.send({"type": "TASK"}))

    assert responses[0].type == MsgType.ERROR
    assert responses[0].payload["code"] == "INVALID_ENVELOPE"
    assert queue.get_nowait() == responses[0]
    logged = (tmp_path / "invalid-envelope" / "messages.jsonl").read_text(
        encoding="utf-8"
    )
    assert Envelope.model_validate_json(logged.strip()).type == MsgType.ERROR


def test_resume_skips_completed_stages(tmp_path: Path):
    raw = _scenario("congestion")
    normalized = normalize(raw)
    assert normalized.telemetry is not None
    counters = {
        name: CountingAgent(name)
        for name in ("normalizer", "performance", "infrastructure", "connectivity")
    }
    state = RunState(
        run_id="resume-run",
        input_ref=raw,
        stage="analysis",
        completed_stages=["normalize", "analysis"],
        telemetry=normalized.telemetry,
        findings={
            "normalizer": {
                "data_quality": normalized.data_quality.model_dump(mode="json")
            },
            "performance": {"agent": "performance"},
            "infrastructure": {"agent": "infrastructure"},
            "connectivity": {"agent": "connectivity"},
        },
    )
    state.save(tmp_path)
    agents = {name: agent for name, agent in counters.items()}
    agents.update({"correlator": StubCorrelator(), "reporter": StubReporter()})

    resumed = asyncio.run(
        Orchestrator(agents, runs_dir=tmp_path).resume(state.run_id)
    )

    assert isinstance(resumed, RunState)
    assert resumed.stage == "complete"
    assert all(agent.calls == 0 for agent in counters.values())
    assert resumed.completed_stages == [
        "normalize", "analysis", "correlator", "reporter"
    ]


def test_query_loop_is_capped_at_two_rounds(tmp_path: Path):
    target = QueryTarget()
    state = asyncio.run(Orchestrator(
        _agents(performance=target, correlator=LoopingCorrelator()),
        runs_dir=tmp_path,
    ).run(_scenario("congestion")))

    assert isinstance(state, RunState)
    assert state.rounds == 2
    assert target.query_calls == 2
    assert "correlator query limit reached after 2 rounds" in state.limitations
