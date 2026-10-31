"""Asynchronous in-process message routing and run logging."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from netcoop.agents.base import Agent
from netcoop.protocol import Envelope, MsgType


class MessageBus:
    """Route validated envelopes to registered agents and publish every message."""

    def __init__(self, run_id: str, runs_dir: str | Path = "runs") -> None:
        self.run_id = run_id
        self.runs_dir = Path(runs_dir)
        self._agents: dict[str, Agent] = {}
        self._subscribers: list[asyncio.Queue[Envelope]] = []
        self._write_lock = asyncio.Lock()

    def register(self, agent: Agent) -> None:
        """Register or replace an agent by its protocol name."""

        self._agents[agent.name] = agent

    def subscribe(self) -> asyncio.Queue[Envelope]:
        """Return a queue that receives envelopes emitted after subscription."""

        queue: asyncio.Queue[Envelope] = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    async def send(self, envelope: Envelope | dict[str, Any] | object) -> list[Envelope]:
        """Validate and deliver an envelope, returning validated agent responses.

        Validation failures are represented as ERROR envelopes so malformed input
        cannot escape the protocol boundary as an exception.
        """

        try:
            message = Envelope.model_validate(envelope)
        except Exception as exc:
            error = self._validation_error(envelope, exc)
            await self._emit(error)
            return [error]

        await self._emit(message)
        agent = self._agents.get(message.recipient)
        if agent is None:
            error = Envelope(
                type=MsgType.ERROR,
                sender="bus",
                recipient=message.sender,
                correlation_id=message.msg_id,
                payload={
                    "code": "RECIPIENT_NOT_FOUND",
                    "message": f"recipient '{message.recipient}' is not registered",
                    "recoverable": True,
                },
            )
            await self._emit(error)
            return [error]

        responses = await agent.handle(message)
        if not isinstance(responses, list):
            responses = [responses]

        validated: list[Envelope] = []
        for response in responses:
            try:
                item = Envelope.model_validate(response)
            except Exception as exc:
                item = self._validation_error(response, exc, recipient=message.sender)
            await self._emit(item)
            validated.append(item)
        return validated

    async def _emit(self, envelope: Envelope) -> None:
        async with self._write_lock:
            run_dir = self.runs_dir / self.run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            with (run_dir / "messages.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(envelope.model_dump_json())
                stream.write("\n")
            for queue in tuple(self._subscribers):
                queue.put_nowait(envelope)

    @staticmethod
    def _validation_error(
        value: object,
        exc: Exception,
        *,
        recipient: str | None = None,
    ) -> Envelope:
        sender = None
        if isinstance(value, dict):
            candidate = value.get("sender")
            if isinstance(candidate, str) and candidate:
                sender = candidate
        return Envelope(
            type=MsgType.ERROR,
            sender="bus",
            recipient=recipient or sender or "orchestrator",
            payload={
                "code": "INVALID_ENVELOPE",
                "message": f"invalid envelope: {exc}",
                "recoverable": True,
            },
        )
