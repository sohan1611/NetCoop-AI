import pytest
from pydantic import ValidationError

from netcoop.protocol import (
    DataQuality,
    Envelope,
    Evidence,
    Finding,
    Hypothesis,
    MsgType,
    Query,
    Report,
    SupportingEvidence,
)


def _models():
    evidence = Evidence(
        metric="latency",
        value=180.0,
        unit="ms",
        threshold=100.0,
        device_id="AP-01",
        timestamp="2026-10-31T08:30:00+00:00",
        interpretation="Latency exceeds the threshold.",
    )
    hypothesis = Hypothesis(cause="congestion", score=0.82)
    data_quality = DataQuality(score=0.9, issues=["One stale sample omitted."])
    finding = Finding(
        agent="performance",
        hypotheses=[hypothesis],
        affected_devices=["AP-01"],
        evidence=[evidence],
        data_quality=data_quality,
        notes=["Sustained for five minutes."],
    )
    query = Query(
        target_agent="performance",
        action="widen_window",
        params={"minutes": 15},
        reason="Top hypotheses are too close.",
    )
    supporting_evidence = SupportingEvidence(
        metric="latency", value=180.0, interpretation="Latency is elevated."
    )
    report = Report(
        incident_id="INC-0001",
        status="diagnosed",
        suspected_root_cause="congestion",
        confidence=0.82,
        confidence_category="high",
        affected_devices=["AP-01"],
        supporting_evidence=[supporting_evidence],
        alternative_causes=["device_overload"],
        recommended_actions=["Reduce traffic on the uplink."],
        evidence_sources=["performance"],
        limitations=[],
        run_id="RUN-0001",
    )
    envelope = Envelope(
        type=MsgType.FINDING,
        sender="performance",
        recipient="correlator",
        correlation_id="task-1",
        payload=finding.model_dump(mode="json"),
    )
    return [
        evidence,
        hypothesis,
        data_quality,
        finding,
        query,
        supporting_evidence,
        report,
        envelope,
    ]


@pytest.mark.parametrize("model", _models())
def test_model_json_round_trip(model):
    restored = type(model).model_validate_json(model.model_dump_json())
    assert restored == model


@pytest.mark.parametrize(
    "invalid_envelope",
    [
        {
            "schema_version": "2.0",
            "type": "TASK",
            "sender": "orchestrator",
            "recipient": "performance",
            "payload": {},
        },
        {
            "type": "UNKNOWN",
            "sender": "orchestrator",
            "recipient": "performance",
            "payload": {},
        },
        {"type": "TASK", "recipient": "performance", "payload": {}},
    ],
)
def test_bad_envelope_is_rejected(invalid_envelope):
    with pytest.raises(ValidationError):
        Envelope.model_validate(invalid_envelope)


def test_envelope_rejects_extra_fields():
    with pytest.raises(ValidationError):
        Envelope.model_validate(
            {
                "type": "TASK",
                "sender": "orchestrator",
                "recipient": "performance",
                "payload": {},
                "unexpected": True,
            }
        )
