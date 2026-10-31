"""Rule-based infrastructure device analysis."""

from __future__ import annotations

from copy import deepcopy
from statistics import fmean
from typing import Any

from netcoop.agents.base import Agent
from netcoop.agents.performance import metric_severity
from netcoop.config import THRESHOLDS
from netcoop.protocol import DataQuality, Envelope, Evidence, Finding, Hypothesis, MsgType


INFRASTRUCTURE_METRICS = ("cpu_pct", "mem_pct", "interface_errors", "queue_drops")
UNITS = {
    "cpu_pct": "%",
    "mem_pct": "%",
    "interface_errors": "count",
    "queue_drops": "count",
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 6)


def status_severity(status: Any) -> float:
    """Return a pure severity for a canonical device status."""

    if not isinstance(status, str):
        return 0.0
    return {"up": 0.0, "degraded": 0.6, "down": 1.0}.get(status.lower(), 0.0)


def metric_baseline_severity(metric: str, latest: float, baseline: float | None) -> float:
    """Score a material increase from earlier device records."""

    if baseline is None:
        return 0.0
    increase = latest - baseline
    floor = max(abs(baseline), THRESHOLDS[metric]["warn"] * 0.25, 1.0)
    if increase <= floor * 0.5:
        return 0.0
    return _clamp(increase / floor)


def filter_devices(telemetry: dict[str, Any], device_id: Any) -> dict[str, Any]:
    """Return a copy containing only one device's infrastructure rows."""

    result = deepcopy(telemetry)
    rows = result.get("devices")
    if isinstance(rows, list):
        result["devices"] = [
            row for row in rows
            if isinstance(row, dict) and row.get("device_id") == device_id
        ]
    return result


def _quality(value: Any) -> DataQuality | None:
    if value is None:
        return None
    try:
        return DataQuality.model_validate(value)
    except Exception:
        return None


def analyze_infrastructure(
    telemetry: dict[str, Any],
    *,
    data_quality: DataQuality | dict[str, Any] | None = None,
    detailed: bool = False,
) -> Finding:
    """Analyze canonical device rows without side effects or exceptions."""

    notes: list[str] = []
    rows = telemetry.get("devices") if isinstance(telemetry, dict) else None
    if not isinstance(rows, list):
        rows = []
        notes.append("Missing or invalid devices section; no infrastructure rows were analyzed.")
    elif not rows:
        notes.append("No infrastructure device rows were supplied.")

    grouped: dict[str, list[dict[str, Any]]] = {}
    invalid_rows = 0
    for row in rows:
        if not isinstance(row, dict):
            invalid_rows += 1
            continue
        device_id = row.get("device_id")
        key = device_id if isinstance(device_id, str) and device_id else "<unknown>"
        grouped.setdefault(key, []).append(row)
    if invalid_rows:
        notes.append(f"Ignored {invalid_rows} malformed infrastructure row(s).")

    evidence: list[Evidence] = []
    affected: list[str] = []
    device_signals: dict[str, dict[str, float]] = {}
    seen_metrics: set[str] = set()
    seen_status = False

    for device_id, device_rows in grouped.items():
        latest_row = device_rows[-1]
        signals: dict[str, float] = {}
        statuses = [row.get("status") for row in device_rows if isinstance(row.get("status"), str)]
        if statuses:
            seen_status = True
            status = statuses[-1].lower()
            status_signal = status_severity(status)
            signals["status"] = status_signal
            if status_signal >= 0.5 or detailed:
                evidence.append(Evidence(
                    metric="status",
                    value=status,
                    unit=None,
                    threshold=None,
                    device_id=None if device_id == "<unknown>" else device_id,
                    timestamp=latest_row.get("timestamp") if isinstance(latest_row.get("timestamp"), str) else None,
                    interpretation=(
                        f"device status is {status}"
                        if status_signal >= 0.5
                        else "device status is up"
                    ),
                ))
            if status_signal >= 0.5 and device_id != "<unknown>" and device_id not in affected:
                affected.append(device_id)

        for metric in INFRASTRUCTURE_METRICS:
            values = [
                value for row in device_rows
                if (value := _number(row.get(metric))) is not None
            ]
            if not values:
                continue
            seen_metrics.add(metric)
            latest = values[-1]
            mean_value = fmean(values)
            baseline = fmean(values[:-1]) if len(values) > 1 else None
            threshold_score = max(metric_severity(metric, latest), metric_severity(metric, mean_value))
            change_score = metric_baseline_severity(metric, latest, baseline)
            signal = max(threshold_score, change_score)
            signals[metric] = signal
            abnormal = signal >= 0.5
            if abnormal or detailed:
                comparisons: list[str] = []
                threshold = THRESHOLDS[metric]["warn"]
                if threshold_score >= 0.5:
                    comparisons.append(f"latest/mean is at or above warning threshold {threshold:g}")
                if change_score >= 0.5 and baseline is not None:
                    comparisons.append(f"latest increased from earlier-record mean {baseline:.2f}")
                evidence.append(Evidence(
                    metric=metric,
                    value=round(latest, 6),
                    unit=UNITS[metric],
                    threshold=threshold,
                    device_id=None if device_id == "<unknown>" else device_id,
                    timestamp=latest_row.get("timestamp") if isinstance(latest_row.get("timestamp"), str) else None,
                    interpretation="; ".join(comparisons) or "metric is within configured and baseline limits",
                ))
                if detailed and len(values) > 1:
                    evidence.append(Evidence(
                        metric=metric,
                        value=round(mean_value, 6),
                        unit=UNITS[metric],
                        threshold=threshold,
                        device_id=None if device_id == "<unknown>" else device_id,
                        timestamp=None,
                        interpretation=f"mean across {len(values)} records; earlier-record baseline was {baseline:.2f}",
                    ))
            if abnormal and device_id != "<unknown>" and device_id not in affected:
                affected.append(device_id)
        device_signals[device_id] = signals

    missing = [metric for metric in INFRASTRUCTURE_METRICS if metric not in seen_metrics]
    if grouped and missing:
        notes.append(f"No usable values supplied for infrastructure metrics: {', '.join(missing)}.")
    if grouped and not seen_status:
        notes.append("No usable device status values were supplied.")
    if "<unknown>" in grouped:
        notes.append("Some device rows lacked device_id; their metrics were analyzed but cannot identify an affected device.")

    cause_scores = {
        "interface_fault": 0.0,
        "device_overload": 0.0,
        "hardware_failure": 0.0,
        "congestion": 0.0,
    }
    for signals in device_signals.values():
        get = lambda metric: signals.get(metric, 0.0)
        status = get("status")
        errors = get("interface_errors")
        drops = get("queue_drops")
        cpu = get("cpu_pct")
        memory = get("mem_pct")
        interface_fault = max(0.9 * errors, 0.75 * errors + 0.15 * drops + 0.10 * status)
        overload = max(0.85 * max(cpu, memory), 0.55 * cpu + 0.45 * memory)
        hardware = max(status, 0.6 * status + 0.1 * errors)
        congestion = max(0.85 * drops, 0.65 * drops + 0.15 * cpu)
        cause_scores["interface_fault"] = max(cause_scores["interface_fault"], interface_fault)
        cause_scores["device_overload"] = max(cause_scores["device_overload"], overload)
        cause_scores["hardware_failure"] = max(cause_scores["hardware_failure"], hardware)
        cause_scores["congestion"] = max(cause_scores["congestion"], congestion)

    cause_scores = {cause: _clamp(score) for cause, score in cause_scores.items()}
    strongest = max(cause_scores.values(), default=0.0)
    if not grouped:
        normal_score = 0.2
    elif strongest == 0:
        normal_score = 0.95
    else:
        normal_score = _clamp(1.0 - strongest)
    cause_scores["normal"] = normal_score
    hypotheses = [
        Hypothesis(cause=cause, score=score)
        for cause, score in sorted(cause_scores.items(), key=lambda item: (-item[1], item[0]))
    ]
    return Finding(
        agent="infrastructure",
        hypotheses=hypotheses,
        affected_devices=affected,
        evidence=evidence,
        data_quality=_quality(data_quality),
        notes=notes,
    )


class InfrastructureAgent(Agent):
    """Diagnose device-health symptoms from canonical device rows."""

    name = "infrastructure"

    def __init__(self) -> None:
        self._last_telemetry: dict[str, Any] = {}
        self._last_data_quality: DataQuality | dict[str, Any] | None = None

    async def handle(self, envelope: Envelope) -> list[Envelope]:
        try:
            if envelope.type == MsgType.TASK:
                raw = envelope.payload.get("telemetry")
                telemetry = raw if isinstance(raw, dict) else {}
                finding = analyze_infrastructure(
                    telemetry,
                    data_quality=envelope.payload.get("data_quality"),
                )
                if not isinstance(raw, dict):
                    finding.notes.append("TASK payload did not contain a canonical telemetry object.")
                self._last_telemetry = telemetry
                self._last_data_quality = envelope.payload.get("data_quality")
                message_type = MsgType.FINDING
            elif envelope.type == MsgType.QUERY:
                finding = self._answer_query(envelope)
                message_type = MsgType.RESPONSE
            else:
                return [self._error(envelope, "infrastructure accepts TASK and QUERY envelopes only")]
            return [Envelope(
                type=message_type,
                sender=self.name,
                recipient=envelope.sender,
                correlation_id=envelope.msg_id,
                payload=finding.model_dump(mode="json"),
            )]
        except Exception as exc:  # Agent protocol boundary must never raise.
            fallback = Finding(
                agent=self.name,
                hypotheses=[Hypothesis(cause="normal", score=0.0)],
                affected_devices=[],
                evidence=[],
                notes=[f"Infrastructure analysis could not complete: {exc}"],
            )
            return [Envelope(
                type=MsgType.RESPONSE if envelope.type == MsgType.QUERY else MsgType.FINDING,
                sender=self.name,
                recipient=envelope.sender,
                correlation_id=envelope.msg_id,
                payload=fallback.model_dump(mode="json"),
            )]

    def _answer_query(self, envelope: Envelope) -> Finding:
        action = envelope.payload.get("action")
        params = envelope.payload.get("params")
        params = params if isinstance(params, dict) else {}
        supplied = envelope.payload.get("telemetry")
        telemetry = supplied if isinstance(supplied, dict) else self._last_telemetry
        if action == "widen_window":
            window = params.get("window", params.get("minutes"))
            finding = analyze_infrastructure(
                telemetry, data_quality=self._last_data_quality, detailed=True
            )
            finding.notes.append(f"QUERY widen_window analyzed all available device records for window {window!r}.")
            return finding
        if action == "device_detail":
            device_id = params.get("device_id")
            finding = analyze_infrastructure(
                filter_devices(telemetry, device_id),
                data_quality=self._last_data_quality,
                detailed=True,
            )
            finding.notes.append(f"QUERY device_detail analyzed device {device_id!r} in detail.")
            return finding
        finding = analyze_infrastructure(
            telemetry, data_quality=self._last_data_quality, detailed=True
        )
        finding.notes.append(f"Unsupported infrastructure query action: {action!r}.")
        return finding

    def _error(self, envelope: Envelope, message: str) -> Envelope:
        return Envelope(
            type=MsgType.ERROR,
            sender=self.name,
            recipient=envelope.sender,
            correlation_id=envelope.msg_id,
            payload={"code": "INFRASTRUCTURE_AGENT_ERROR", "message": message, "recoverable": True},
        )
