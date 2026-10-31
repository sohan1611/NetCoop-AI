"""Validated message and report models shared by NetCoop agents."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


def _new_message_id() -> str:
    return str(uuid4())


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class MsgType(str, Enum):
    TASK = "TASK"
    FINDING = "FINDING"
    QUERY = "QUERY"
    RESPONSE = "RESPONSE"
    ERROR = "ERROR"


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    msg_id: str = Field(default_factory=_new_message_id)
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    type: MsgType
    sender: str
    recipient: str
    correlation_id: str | None = None
    timestamp: str = Field(default_factory=_utc_timestamp)
    payload: dict[str, Any]


class Evidence(BaseModel):
    metric: str
    value: float | int | str
    unit: str | None = None
    threshold: float | None = None
    device_id: str | None = None
    timestamp: str | None = None
    interpretation: str


class Hypothesis(BaseModel):
    cause: str
    score: float = Field(ge=0.0, le=1.0)


class DataQuality(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    issues: list[str]


class Finding(BaseModel):
    agent: str
    hypotheses: list[Hypothesis]
    affected_devices: list[str]
    evidence: list[Evidence]
    data_quality: DataQuality | None = None
    notes: list[str]


class Query(BaseModel):
    target_agent: str
    action: str
    params: dict[str, Any]
    reason: str


class SupportingEvidence(BaseModel):
    metric: str
    value: float | int | str
    interpretation: str
    device_id: str | None = None
    unit: str | None = None
    threshold: float | None = None
    baseline: float | None = None
    timestamp: str | None = None


class Report(BaseModel):
    incident_id: str
    status: Literal["diagnosed", "insufficient_evidence", "error"]
    suspected_root_cause: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_category: Literal["high", "medium", "low", "insufficient"]
    affected_devices: list[str]
    supporting_evidence: list[SupportingEvidence]
    alternative_causes: list[str]
    recommended_actions: list[str]
    evidence_sources: list[str]
    limitations: list[str]
    run_id: str
