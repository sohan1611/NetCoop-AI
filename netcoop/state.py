"""Persisted checkpoints for resumable diagnosis runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class RunState(BaseModel):
    """Serializable progress and evidence accumulated by the orchestrator."""

    run_id: str
    input_ref: Any
    stage: str = "pending"
    completed_stages: list[str] = Field(default_factory=list)
    telemetry: dict[str, Any] | None = None
    findings: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    rounds: int = 0

    def save(self, runs_dir: str | Path = "runs") -> Path:
        """Write this checkpoint to ``runs/<run_id>/state.json``."""

        run_dir = Path(runs_dir) / self.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "state.json"
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, run_id: str, runs_dir: str | Path = "runs") -> "RunState":
        """Load and validate a previously saved checkpoint."""

        path = Path(runs_dir) / run_id / "state.json"
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
