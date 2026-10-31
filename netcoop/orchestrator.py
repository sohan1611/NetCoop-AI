"""Resumable orchestration for the NetCoop diagnosis pipeline."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

from netcoop.agents.base import Agent
from netcoop.bus import MessageBus
from netcoop.protocol import Envelope, MsgType, Report
from netcoop.state import RunState


ANALYSIS_AGENTS = ("performance", "infrastructure", "connectivity")


class Orchestrator:
    """Run registered agents through the diagnosis stages.

    The registry is deliberately pluggable: T7 and T8 can provide agents named
    ``correlator`` and ``reporter`` without coupling this module to their classes.
    """

    def __init__(
        self,
        agents: dict[str, Agent],
        runs_dir: str | Path = "runs",
        timeout_s: float = 10,
        retries: int = 1,
        fail_agents: set[str] | None = None,
    ) -> None:
        self.agents = dict(agents)
        self.runs_dir = Path(runs_dir)
        self.timeout_s = timeout_s
        self.retries = max(0, retries)
        self.fail_agents = set(fail_agents or ())
        self.bus: MessageBus | None = None

    async def run(
        self,
        raw: str | bytes | dict[str, Any] | list[Any],
        run_id: str | None = None,
    ) -> RunState | dict[str, Any]:
        """Start a diagnosis, returning state or a clear normalization error."""

        state = RunState(
            run_id=run_id or str(uuid4()),
            input_ref=self._serializable_input(raw),
        )
        state.save(self.runs_dir)
        return await self._continue(state)

    async def resume(self, run_id: str) -> RunState | dict[str, Any]:
        """Continue the first stage not present in the saved completion list."""

        state = RunState.load(run_id, self.runs_dir)
        return await self._continue(state)

    async def _continue(self, state: RunState) -> RunState | dict[str, Any]:
        self.bus = MessageBus(state.run_id, self.runs_dir)
        for agent in self.agents.values():
            self.bus.register(agent)

        if "normalize" not in state.completed_stages:
            error = await self._normalize(state)
            if error is not None:
                return error
        if "analysis" not in state.completed_stages:
            await self._analyze(state)
        if "correlator" not in state.completed_stages:
            await self._correlate(state)
        if "reporter" not in state.completed_stages:
            await self._report(state)
        return state

    async def _normalize(self, state: RunState) -> dict[str, Any] | None:
        state.stage = "normalize"
        task = Envelope(
            type=MsgType.TASK,
            sender="orchestrator",
            recipient="normalizer",
            correlation_id=state.run_id,
            payload={"raw": state.input_ref},
        )
        responses, reason = await self._call_agent("normalizer", task)
        if reason is not None:
            return self._normalization_error(state, reason)

        error = next((item for item in responses if item.type == MsgType.ERROR), None)
        if error is not None:
            return self._normalization_error(
                state, str(error.payload.get("message", "normalization failed"))
            )
        finding = next(
            (item for item in responses if item.type == MsgType.FINDING), None
        )
        telemetry = finding.payload.get("telemetry") if finding is not None else None
        if not isinstance(telemetry, dict):
            return self._normalization_error(
                state, "normalizer returned no canonical telemetry"
            )

        state.telemetry = telemetry
        state.findings["normalizer"] = finding.payload
        self._complete_stage(state, "normalize")
        return None

    async def _analyze(self, state: RunState) -> None:
        state.stage = "analysis"
        normalizer = state.findings.get("normalizer", {})
        data_quality = (
            normalizer.get("data_quality") if isinstance(normalizer, dict) else None
        )

        async def invoke(name: str) -> tuple[str, list[Envelope], str | None]:
            task = Envelope(
                type=MsgType.TASK,
                sender="orchestrator",
                recipient=name,
                correlation_id=state.run_id,
                payload={
                    "telemetry": state.telemetry or {},
                    "data_quality": data_quality,
                },
            )
            responses, reason = await self._call_agent(name, task)
            return name, responses, reason

        results = await asyncio.gather(*(invoke(name) for name in ANALYSIS_AGENTS))
        for name, responses, reason in results:
            if reason is not None:
                self._add_limitation(state, f"{name} unavailable: {reason}")
                continue
            error = next((item for item in responses if item.type == MsgType.ERROR), None)
            if error is not None:
                message = str(error.payload.get("message", "agent returned an error"))
                self._add_limitation(state, f"{name} unavailable: {message}")
                continue
            finding = next(
                (item for item in responses if item.type == MsgType.FINDING), None
            )
            if finding is None:
                self._add_limitation(state, f"{name} unavailable: no finding returned")
                continue
            state.findings[name] = finding.payload
        self._complete_stage(state, "analysis")

    async def _correlate(self, state: RunState) -> None:
        state.stage = "correlator"
        if "correlator" not in self.agents:
            self._add_limitation(state, "correlator not-installed")
            self._ensure_diagnosis(state)
            self._complete_stage(state, "correlator")
            return

        task = Envelope(
            type=MsgType.TASK,
            sender="orchestrator",
            recipient="correlator",
            correlation_id=state.run_id,
            payload={
                "findings": self._analysis_findings(state),
                "telemetry": state.telemetry or {},
                "limitations": state.limitations,
                "round": state.rounds,
            },
        )
        outputs, reason = await self._call_agent("correlator", task)
        if reason is not None:
            self._add_limitation(state, f"correlator unavailable: {reason}")
            self._ensure_diagnosis(state)
            self._complete_stage(state, "correlator")
            return

        queries =self._record_correlator_outputs(state, outputs)
        while queries and state.rounds < 2:
            state.rounds += 1
            feedback: list[Envelope] = []
            query_results = await asyncio.gather(
                *(self._route_query(state, query) for query in queries)
            )
            for responses in query_results:
                feedback.extend(responses)

            next_queries: list[Envelope] = []
            for response in feedback:
                outputs, reason = await self._call_agent("correlator", response)
                if reason is not None:
                    self._add_limitation(state, f"correlator unavailable: {reason}")
                    next_queries = []
                    break
                next_queries.extend(self._record_correlator_outputs(state, outputs))
            queries = next_queries
            state.save(self.runs_dir)

        if queries:
            self._add_limitation(state, "correlator query limit reached after 2 rounds")
        self._ensure_diagnosis(state)
        self._complete_stage(state, "correlator")

    def _ensure_diagnosis(self, state: RunState) -> None:
        """Guarantee a diagnosis exists so the reporter can always run."""

        correlator = state.findings.get("correlator")
        if isinstance(correlator, dict) and isinstance(
            correlator.get("diagnosis"), dict
        ):
            return
        evidence: list[dict[str, Any]] = []
        devices: list[str] = []
        sources: list[str] = []
        for finding in self._analysis_findings(state):
            for device in finding.get("affected_devices", []):
                if device not in devices:
                    devices.append(device)
            for item in finding.get("evidence", []):
                if not isinstance(item, dict):
                    continue
                row = {
                    "metric": item.get("metric"),
                    "value": item.get("value"),
                    "interpretation": item.get("interpretation", ""),
                }
                if row["metric"] is not None and row["value"] is not None and row not in evidence:
                    evidence.append(row)
            if finding.get("agent") and finding["agent"] not in sources:
                sources.append(finding["agent"])
        state.findings["correlator"] = {
            "agent": "correlator",
            "diagnosis": {
                "status": "insufficient_evidence",
                "cause": "insufficient_evidence",
                "confidence": 0.0,
                "confidence_category": "insufficient",
                "affected_devices": devices,
                "alternatives": [],
                "evidence": evidence,
                "evidence_sources": sources,
                "conflicts": [],
            },
        }

    async def _route_query(
        self, state: RunState, query: Envelope
    ) -> list[Envelope]:
        target = query.payload.get("target_agent")
        if not isinstance(target, str) or not target:
            target = query.recipient
        payload = dict(query.payload)
        payload.setdefault("telemetry", state.telemetry or {})
        routed = query.model_copy(update={"recipient": target, "payload": payload})
        responses, reason = await self._call_agent(target, routed)
        if reason is not None:
            self._add_limitation(state, f"{target} unavailable: {reason}")
            return [Envelope(
                type=MsgType.ERROR,
                sender=target,
                recipient="correlator",
                correlation_id=query.msg_id,
                payload={
                    "code": "QUERY_TARGET_UNAVAILABLE",
                    "message": reason,
                    "recoverable": True,
                },
            )]

        feedback: list[Envelope] = []
        for response in responses:
            if response.type not in (MsgType.RESPONSE, MsgType.ERROR):
                response = response.model_copy(update={"type": MsgType.RESPONSE})
            if response.recipient != "correlator":
                response = response.model_copy(update={"recipient": "correlator"})
            feedback.append(response)
        return feedback

    async def _report(self, state: RunState) -> None:
        state.stage = "reporter"
        if "reporter" not in self.agents:
            self._add_limitation(state, "reporter not-installed")
            state.findings["reporter"] = {
                "report": self._fallback_report(state).model_dump(mode="json"),
                "narrative": None,
            }
            self._complete_stage(state, "reporter", final=True)
            return

        task = Envelope(
            type=MsgType.TASK,
            sender="orchestrator",
            recipient="reporter",
            correlation_id=state.run_id,
            payload={
                "run_id": state.run_id,
                "telemetry": state.telemetry or {},
                "findings": state.findings,
                "limitations": state.limitations,
                "rounds": state.rounds,
            },
        )
        responses, reason = await self._call_agent("reporter", task)
        if reason is not None:
            self._add_limitation(state, f"reporter unavailable: {reason}")
        else:
            error = next((item for item in responses if item.type == MsgType.ERROR), None)
            if error is not None:
                message = str(error.payload.get("message", "agent returned an error"))
                self._add_limitation(state, f"reporter unavailable: {message}")
            else:
                result = next(iter(responses), None)
                if result is not None:
                    state.findings["reporter"] = result.payload
        if not isinstance(state.findings.get("reporter"), dict):
            state.findings["reporter"] = {
                "report": self._fallback_report(state).model_dump(mode="json"),
                "narrative": None,
            }
        self._complete_stage(state, "reporter", final=True)

    def _fallback_report(self, state: RunState) -> Report:
        self._ensure_diagnosis(state)
        diagnosis = state.findings["correlator"]["diagnosis"]
        telemetry = state.telemetry or {}
        return Report(
            incident_id=str(telemetry.get("incident_id", "unknown")),
            status=diagnosis["status"],
            suspected_root_cause=(
                "Evidence is insufficient to identify a root cause; collect more data."
                if diagnosis["status"] == "insufficient_evidence"
                else str(diagnosis["cause"]).replace("_", " ")
            ),
            confidence=diagnosis["confidence"],
            confidence_category=diagnosis["confidence_category"],
            affected_devices=diagnosis["affected_devices"],
            supporting_evidence=diagnosis["evidence"],
            alternative_causes=[
                f"{a['cause'].replace('_', ' ')} (confidence {a['confidence']:.1%})"
                for a in diagnosis.get("alternatives", [])
            ],
            recommended_actions=["Re-run the diagnosis once the failed component is restored."],
            evidence_sources=diagnosis["evidence_sources"],
            limitations=list(dict.fromkeys(state.limitations)),
            run_id=state.run_id,
        )

    async def _call_agent(
        self, name: str, envelope: Envelope
    ) -> tuple[list[Envelope], str | None]:
        if name not in self.agents:
            return [], "not-installed"
        if self.bus is None:
            return [], "message bus is not initialized"

        reason = "unknown failure"
        for _ in range(self.retries + 1):
            try:
                if name in self.fail_agents:
                    raise RuntimeError("injected failure")
                responses = await asyncio.wait_for(
                    self.bus.send(envelope), timeout=self.timeout_s
                )
                return responses, None
            except TimeoutError:
                reason = f"timeout after {self.timeout_s:g}s"
            except Exception as exc:
                reason = str(exc) or type(exc).__name__
        return [], reason

    def _record_correlator_outputs(
        self, state: RunState, outputs: list[Envelope]
    ) -> list[Envelope]:
        queries: list[Envelope] = []
        for output in outputs:
            if output.type == MsgType.QUERY:
                queries.append(output)
            elif output.type == MsgType.ERROR:
                message = str(output.payload.get("message", "agent returned an error"))
                self._add_limitation(state, f"correlator unavailable: {message}")
            else:
                state.findings["correlator"] = output.payload
        return queries

    @staticmethod
    def _analysis_findings(state: RunState) -> list[dict[str, Any]]:
        return [
            state.findings[name]
            for name in ANALYSIS_AGENTS
            if isinstance(state.findings.get(name), dict)
        ]

    def _normalization_error(
        self, state: RunState, message: str
    ) -> dict[str, Any]:
        state.stage = "error"
        self._add_limitation(state, f"normalizer unavailable: {message}")
        state.save(self.runs_dir)
        return {
            "status": "error",
            "run_id": state.run_id,
            "error": message,
            "limitations": state.limitations,
        }

    def _complete_stage(
        self, state: RunState, stage: str, *, final: bool = False
    ) -> None:
        if stage not in state.completed_stages:
            state.completed_stages.append(stage)
        state.stage = "complete" if final else stage
        state.save(self.runs_dir)

    @staticmethod
    def _add_limitation(state: RunState, limitation: str) -> None:
        if limitation not in state.limitations:
            state.limitations.append(limitation)

    @staticmethod
    def _serializable_input(raw: Any) -> Any:
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return raw
