"""Rule-based performance telemetry analysis."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from statistics import fmean
from typing import Any

from netcoop.agents.base import Agent
from netcoop.config import THRESHOLDS
from netcoop.protocol import DataQuality, Envelope, Evidence, Finding, Hypothesis, MsgType


PERFORMANCE_METRICS = (
    "latency_ms",
    "jitter_ms",
    "packet_loss_pct",
    "throughput_mbps",
    "link_utilization_pct",
    "rssi_dbm",
    "retransmission_pct",
)
UNITS = {
    "latency_ms": "ms",
    "jitter_ms": "ms",
    "packet_loss_pct": "%",
    "throughput_mbps": "Mbps",
    "bandwidth_mbps": "Mbps",
    "link_utilization_pct": "%",
    "rssi_dbm": "dBm",
    "retransmission_pct": "%",
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 6)


def metric_severity(metric: str, value: float) -> float:
    """Return a pure 0..1 warning severity for a configured metric."""

    limits = THRESHOLDS.get(metric)
    if limits is None:
        return 0.0
    warn, crit = limits["warn"], limits["crit"]
    if metric == "rssi_dbm":
        if value > warn:
            return 0.0
        if value <= crit:
            return 1.0
        return _clamp(0.5 + 0.5 * (warn - value) / (warn - crit))
    if value < warn:
        return 0.0
    if value >= crit:
        return 1.0
    return _clamp(0.5 + 0.5 * (value - warn) / (crit - warn))


def baseline_change_severity(metric: str, latest: float, baseline: float | None) -> float:
    """Score a material deterioration from earlier samples."""

    if baseline is None:
        return 0.0
    if metric == "throughput_mbps":
        if baseline <= 0 or latest >= baseline * 0.7:
            return 0.0
        return _clamp((baseline - latest) / baseline)
    if metric == "rssi_dbm":
        decline = baseline - latest
        return _clamp(decline / 10.0) if decline >= 5.0 else 0.0
    increase = latest - baseline
    floor = max(abs(baseline), THRESHOLDS.get(metric, {}).get("warn", 1.0) * 0.25, 1.0)
    if increase <= floor * 0.5:
        return 0.0
    return _clamp(increase / floor)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sorted_samples(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = list(enumerate(rows))
    indexed.sort(key=lambda item: (_parse_timestamp(item[1].get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc), item[0]))
    return [row for _, row in indexed]


def filter_samples_by_window(telemetry: dict[str, Any], window: Any) -> dict[str, Any]:
    """Return a copy containing samples within the requested recent minutes."""

    result = deepcopy(telemetry)
    rows = result.get("samples")
    if not isinstance(rows, list):
        result["samples"] = []
        return result
    try:
        if isinstance(window, str):
            window = window.strip().lower().removesuffix("minutes").removesuffix("minute").removesuffix("min").removesuffix("m").strip()
        minutes = float(window)
    except (TypeError, ValueError):
        return result
    dated = [parsed for row in rows if isinstance(row, dict) and (parsed := _parse_timestamp(row.get("timestamp"))) is not None]
    if minutes <= 0 or not dated:
        return result
    cutoff = max(dated) - timedelta(minutes=minutes)
    result["samples"] = [
        row for row in rows
        if not isinstance(row, dict)
        or (parsed := _parse_timestamp(row.get("timestamp"))) is None
        or parsed >= cutoff
    ]
    return result


def filter_telemetry_for_device(telemetry: dict[str, Any], device_id: Any) -> dict[str, Any]:
    """Return a copy limited to one device's device and sample rows."""

    result = deepcopy(telemetry)
    for section in ("devices", "samples"):
        rows = result.get(section)
        if isinstance(rows, list):
            result[section] = [
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


def analyze_performance(
    telemetry: dict[str, Any],
    *,
    data_quality: DataQuality | dict[str, Any] | None = None,
    detailed: bool = False,
) -> Finding:
    """Analyze canonical telemetry without side effects or exceptions."""

    notes: list[str] = []
    rows = telemetry.get("samples") if isinstance(telemetry, dict) else None
    if not isinstance(rows, list):
        rows = []
        notes.append("Missing or invalid samples section; no performance samples were analyzed.")
    elif not rows:
        notes.append("No performance samples were supplied.")

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
        notes.append(f"Ignored {invalid_rows} malformed performance sample row(s).")

    evidence: list[Evidence] = []
    affected: list[str] = []
    device_signals: dict[str, dict[str, float]] = {}
    seen_metrics: set[str] = set()

    for device_id, device_rows in grouped.items():
        ordered = _sorted_samples(device_rows)
        signals: dict[str, float] = {}
        for metric in PERFORMANCE_METRICS:
            observations = [
                (value, row.get("timestamp"))
                for row in ordered
                if (value := _number(row.get(metric))) is not None
            ]
            if not observations:
                continue
            seen_metrics.add(metric)
            values = [item[0] for item in observations]
            latest, timestamp = observations[-1]
            mean_value = fmean(values)
            baseline = fmean(values[:-1]) if len(values) > 1 else None
            threshold_score = max(metric_severity(metric, latest), metric_severity(metric, mean_value))
            change_score = baseline_change_severity(metric, latest, baseline)
            signal = max(threshold_score, change_score)
            signals[metric] = signal

            threshold = THRESHOLDS.get(metric, {}).get("warn")
            abnormal = signal >= 0.5
            if abnormal or detailed:
                comparisons: list[str] = []
                evidence_value = latest
                if threshold_score >= 0.5 and threshold is not None:
                    inverted = metric == "rssi_dbm"
                    direction = "at or below" if inverted else "at or above"
                    latest_warns = latest <= threshold if inverted else latest >= threshold
                    if latest_warns:
                        comparisons.append(f"latest value is {direction} the warning threshold {threshold:g}")
                    else:
                        evidence_value = mean_value
                        healthy_direction = "above" if inverted else "below"
                        comparisons.append(
                            f"window mean over {len(values)} samples is {direction} the warning threshold {threshold:g}; "
                            f"latest {latest:g} at {timestamp} is {healthy_direction} it"
                        )
                if change_score >= 0.5 and baseline is not None:
                    comparisons.append(f"latest deteriorated from earlier-sample mean {baseline:.2f}")
                interpretation = "; ".join(comparisons) or "metric is within configured and baseline limits"
                evidence.append(Evidence(
                    metric=metric,
                    value=round(evidence_value, 6),
                    unit=UNITS[metric],
                    threshold=threshold if threshold is not None else (round(baseline * 0.7, 6) if baseline is not None else None),
                    device_id=None if device_id == "<unknown>" else device_id,
                    timestamp=timestamp if isinstance(timestamp, str) else None,
                    interpretation=interpretation,
                ))
                if detailed and len(values) > 1:
                    evidence.append(Evidence(
                        metric=metric,
                        value=round(mean_value, 6),
                        unit=UNITS[metric],
                        threshold=threshold,
                        device_id=None if device_id == "<unknown>" else device_id,
                        timestamp=None,
                        interpretation=f"mean across {len(values)} samples; earlier-sample baseline was {baseline:.2f}",
                    ))
            if abnormal and device_id != "<unknown>" and device_id not in affected:
                affected.append(device_id)
        device_signals[device_id] = signals

    if grouped and len(seen_metrics) < len(PERFORMANCE_METRICS):
        missing = ", ".join(metric for metric in PERFORMANCE_METRICS if metric not in seen_metrics)
        notes.append(f"No usable values supplied for performance metrics: {missing}.")
    if "<unknown>" in grouped:
        notes.append("Some samples lacked device_id; their metrics were analyzed but cannot identify an affected device.")

    cause_scores = {"congestion": 0.0, "weak_wifi": 0.0, "latency_degradation": 0.0}
    for signals in device_signals.values():
        get = lambda metric: signals.get(metric, 0.0)
        congestion = max(
            0.85 * get("link_utilization_pct"),
            0.45 * get("link_utilization_pct")
            + 0.20 * get("packet_loss_pct")
            + 0.15 * get("jitter_ms")
            + 0.10 * get("retransmission_pct")
            + 0.10 * get("throughput_mbps")
            + 0.10 * get("latency_ms"),
        )
        weak_wifi = max(
            0.9 * get("rssi_dbm"),
            0.65 * get("rssi_dbm")
            + 0.25 * get("retransmission_pct")
            + 0.10 * get("packet_loss_pct"),
        )
        if "rssi_dbm" not in signals:
            weak_wifi *= 0.55
        latency = max(
            0.7 * get("latency_ms"),
            0.55 * get("latency_ms") + 0.20 * get("jitter_ms") + 0.15 * get("packet_loss_pct"),
        )
        cause_scores["congestion"] = max(cause_scores["congestion"], congestion)
        cause_scores["weak_wifi"] = max(cause_scores["weak_wifi"], weak_wifi)
        cause_scores["latency_degradation"] = max(cause_scores["latency_degradation"], latency)

    cause_scores = {cause: _clamp(score) for cause, score in cause_scores.items()}
    strongest = max(cause_scores.values(), default=0.0)
    if not seen_metrics:
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
        agent="performance",
        hypotheses=hypotheses,
        affected_devices=affected,
        evidence=evidence,
        data_quality=_quality(data_quality),
        notes=notes,
    )


class PerformanceAgent(Agent):
    """Diagnose performance symptoms from canonical sample rows."""

    name = "performance"

    def __init__(self) -> None:
        self._last_telemetry: dict[str, Any] = {}
        self._last_data_quality: DataQuality | dict[str, Any] | None = None

    async def handle(self, envelope: Envelope) -> list[Envelope]:
        try:
            if envelope.type == MsgType.TASK:
                raw = envelope.payload.get("telemetry")
                telemetry = raw if isinstance(raw, dict) else {}
                finding = analyze_performance(
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
                return [self._error(envelope, "performance accepts TASK and QUERY envelopes only")]
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
                notes=[f"Performance analysis could not complete: {exc}"],
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
            selected = filter_samples_by_window(telemetry, window)
            finding = analyze_performance(selected, data_quality=self._last_data_quality, detailed=True)
            finding.notes.append(f"QUERY widen_window analyzed the recent window {window!r} in detail.")
            return finding
        if action == "device_detail":
            device_id = params.get("device_id")
            selected = filter_telemetry_for_device(telemetry, device_id)
            finding = analyze_performance(selected, data_quality=self._last_data_quality, detailed=True)
            finding.notes.append(f"QUERY device_detail analyzed device {device_id!r} in detail.")
            return finding
        finding = analyze_performance(telemetry, data_quality=self._last_data_quality, detailed=True)
        finding.notes.append(f"Unsupported performance query action: {action!r}.")
        return finding

    def _error(self, envelope: Envelope, message: str) -> Envelope:
        return Envelope(
            type=MsgType.ERROR,
            sender=self.name,
            recipient=envelope.sender,
            correlation_id=envelope.msg_id,
            payload={"code": "PERFORMANCE_AGENT_ERROR", "message": message, "recoverable": True},
        )
