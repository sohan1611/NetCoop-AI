"""Stateful, deterministic correlation of specialist agent findings.

Each present signature metric is graded from 0 at/below its warning threshold
to 1 at its critical threshold (linearly in between and inverted for RSSI and
service success).  The correlator computes::

    match = sum(weight_i * grade_i) / sum(weight_i)
    coverage = present signature metrics / all signature metrics
    confidence = match * (0.5 + 0.5 * coverage) * dq_factor * agreement_factor

``agreement_factor`` ranges from 0.7 to 1.0 based on how many agents rank the
same cause first.  Relevance-weighted agent scores break close telemetry ties.
Optional null radio fields are represented by coverage rather than charged
again as data-quality loss.  Low confidence or a top-two margin below 0.15
causes one QUERY at a time.  RESPONSE evidence is merged into run state keyed
by ``correlation_id``; after two rounds the agent always returns a FINDING.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import median
from typing import Any

from netcoop.agents.base import Agent
from netcoop.config import (
    AGENT_RELEVANCE,
    AGENT_SUPPORT_RANK_WEIGHT,
    AGREEMENT_FACTOR_MAX,
    AGREEMENT_FACTOR_MIN,
    ALTERNATIVE_CONFIDENCE_WINDOW,
    INSUFFICIENT_CONFIDENCE_THRESHOLD,
    MAX_CORRELATION_ROUNDS,
    MAX_SUPPORTING_EVIDENCE,
    MIN_SIGNATURE_COVERAGE,
    OPTIONAL_RADIO_DQ_ISSUE_FRAGMENTS,
    QUERY_CONFIDENCE_THRESHOLD,
    QUERY_MARGIN_THRESHOLD,
    SERVICE_THRESHOLDS,
    SIGNATURES,
    THRESHOLDS,
)
from netcoop.protocol import (
    DataQuality,
    Envelope,
    Evidence,
    Finding,
    Hypothesis,
    MsgType,
    Query,
)


_DEVICE_METRICS = {
    "cpu_pct", "mem_pct", "interface_errors", "queue_drops", "status"
}
_UNITS = {
    "latency_ms": "ms",
    "jitter_ms": "ms",
    "packet_loss_pct": "%",
    "link_utilization_pct": "%",
    "rssi_dbm": "dBm",
    "retransmission_pct": "%",
    "cpu_pct": "%",
    "mem_pct": "%",
    "interface_errors": "count",
    "queue_drops": "count",
    "dns_success_rate_pct": "%",
    "dhcp_success_rate_pct": "%",
    "dns_response_ms": "ms",
    "dhcp_response_ms": "ms",
}


@dataclass(slots=True)
class _Observation:
    metric: str
    value: float | int | str
    grade: float
    device_id: str | None = None
    unit: str | None = None


@dataclass(slots=True)
class _Candidate:
    cause: str
    match: float
    coverage: float
    confidence: float
    agreement: float
    support: float
    rank: float
    observations: dict[str, _Observation]
    telemetry: dict[str, Any] | None = None
    limitations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _ScoreResult:
    winner: _Candidate
    ordered: list[_Candidate]
    margin: float
    conflicts: list[str]
    resolution: str | None
    unresolved_conflict: bool


@dataclass(slots=True)
class _RunContext:
    run_id: str
    requester: str
    findings: dict[str, dict[str, Any]]
    telemetry: dict[str, Any]
    limitations: list[str]
    round: int
    queries_sent: set[tuple[str, str, str]] = field(default_factory=set)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _threshold_grade(metric: str, value: float) -> float:
    service_metric = metric.removeprefix("dns_").removeprefix("dhcp_")
    limits = THRESHOLDS.get(metric) or SERVICE_THRESHOLDS.get(service_metric)
    if limits is None:
        return 0.0
    warn, crit = limits["warn"], limits["crit"]
    inverted = metric == "rssi_dbm" or service_metric == "success_rate_pct"
    if inverted:
        if value >= warn:
            return 0.0
        if value <= crit:
            return 1.0
        return _clamp((warn - value) / (warn - crit))
    if value <= warn:
        return 0.0
    if value >= crit:
        return 1.0
    return _clamp((value - warn) / (crit - warn))


def _status_grade(value: Any) -> float:
    if not isinstance(value, str):
        return 0.0
    return {"up": 0.0, "degraded": 0.5, "critical": 1.0, "down": 1.0}.get(
        value.lower(), 0.0
    )


def _quality_details(
    findings: dict[str, dict[str, Any]],
) -> tuple[float, list[str], dict[str, Any] | None]:
    scores: list[float] = []
    issues: list[str] = []
    seen_issues: set[str] = set()
    quality_payload: dict[str, Any] | None = None
    for finding in findings.values():
        quality = finding.get("data_quality")
        if not isinstance(quality, dict):
            continue
        score = _number(quality.get("score"))
        if score is not None:
            scores.append(_clamp(score))
        raw_issues = quality.get("issues")
        if isinstance(raw_issues, list):
            for issue in raw_issues:
                if isinstance(issue, str) and issue not in seen_issues:
                    seen_issues.add(issue)
                    issues.append(issue)
        if quality_payload is None:
            quality_payload = quality
    if not scores:
        return 1.0, issues, quality_payload
    reported = min(scores)
    if not issues:
        return reported, issues, quality_payload
    optional_count = sum(
        any(fragment in issue for fragment in OPTIONAL_RADIO_DQ_ISSUE_FRAGMENTS)
        for issue in issues
    )
    factor = reported + (1.0 - reported) * optional_count / len(issues)
    return _clamp(factor), issues, quality_payload


def _service_parts(metric: str) -> tuple[str, str] | None:
    for service in ("dns", "dhcp"):
        prefix = f"{service}_"
        if metric.startswith(prefix):
            return service, metric.removeprefix(prefix)
    return None


def _rows_for_metric(
    telemetry: dict[str, Any], metric: str
) -> list[tuple[Any, str | None]]:
    service_parts = _service_parts(metric)
    if service_parts is not None:
        service, field_name = service_parts
        rows = telemetry.get("services")
        if not isinstance(rows, list):
            return []
        return [
            (row.get(field_name), row.get("server_id"))
            for row in rows
            if isinstance(row, dict)
            and str(row.get("name", "")).lower() == service
            and field_name in row
        ]
    section = "devices" if metric in _DEVICE_METRICS else "samples"
    rows = telemetry.get(section)
    if not isinstance(rows, list):
        return []
    return [
        (row.get(metric), row.get("device_id"))
        for row in rows
        if isinstance(row, dict) and metric in row
    ]


def _device_down(telemetry: dict[str, Any]) -> bool:
    devices = telemetry.get("devices")
    if not isinstance(devices, list):
        return False
    statuses: dict[str | None, set[str]] = {}
    for row in devices:
        if isinstance(row, dict) and isinstance(row.get("status"), str):
            statuses.setdefault(row.get("device_id"), set()).add(row["status"].lower())
    return any(values == {"down"} for values in statuses.values())


def _graded_rows(
    telemetry: dict[str, Any], metric: str, critical_status: bool
) -> list[tuple[float, str | None]]:
    """(grade, device_id) for every row that reports ``metric`` (worse = higher)."""
    result: list[tuple[float, str | None]] = []
    for raw_value, raw_device in _rows_for_metric(telemetry, metric):
        if metric == "status" or metric.endswith("_status"):
            if not isinstance(raw_value, str):
                continue
            grade = _status_grade(raw_value)
            if metric == "status" and critical_status and grade >= 0.5:
                grade = 1.0
        else:
            value = _number(raw_value)
            if value is None:
                continue
            grade = _threshold_grade(metric, value)
        result.append((_clamp(grade), raw_device if isinstance(raw_device, str) else None))
    return result


def _limits(metric: str) -> dict[str, float] | None:
    service_metric = metric.removeprefix("dns_").removeprefix("dhcp_")
    return THRESHOLDS.get(metric) or SERVICE_THRESHOLDS.get(service_metric)


def _describe(metric: str, value: Any, device_id: str | None, unit: str | None) -> str | None:
    """'link_utilization_pct 94 % on CORE_RTR_01 >= critical 90 %' for numeric metrics."""
    number = _number(value)
    limits = _limits(metric)
    if number is None or limits is None:
        return None
    warn, crit = limits["warn"], limits["crit"]
    inverted = metric == "rssi_dbm" or metric.endswith("success_rate_pct")
    suffix = f" {unit}" if unit else ""
    if inverted:
        phrase = (f"<= critical {crit:g}{suffix}" if number <= crit
                  else f"<= warning {warn:g}{suffix}" if number <= warn
                  else f"within limits (warning at {warn:g}{suffix})")
    else:
        phrase = (f">= critical {crit:g}{suffix}" if number >= crit
                  else f">= warning {warn:g}{suffix}" if number >= warn
                  else f"below warning {warn:g}{suffix}")
    where = f" on {device_id}" if device_id else ""
    return f"{metric} {number:g}{suffix}{where} {phrase}"


def _enrich_evidence(
    evidence: list[dict[str, Any]], telemetry: dict[str, Any], affected: list[str]
) -> list[dict[str, Any]]:
    """Fill unit/threshold/interpretation and a baseline (median of the same metric on
    unaffected devices, or None) so every evidence item is self-describing."""
    skip = set(affected)
    evidence = [dict(item) for item in evidence]  # never mutate the agents' findings
    for item in evidence:
        metric = item.get("metric")
        if not isinstance(metric, str):
            continue
        item.setdefault("baseline", None)
        if item.get("unit") is None:
            item["unit"] = _UNITS.get(metric)
        limits = _limits(metric)
        if item.get("threshold") is None and limits is not None:
            item["threshold"] = limits["warn"]
        text = _describe(metric, item.get("value"), item.get("device_id"), item.get("unit"))
        if text is not None:
            item["interpretation"] = text
        if _service_parts(metric) is None and metric not in {"status"}:
            others = [
                number for raw, device in _rows_for_metric(telemetry, metric)
                if device not in skip and (number := _number(raw)) is not None
            ]
            if others:
                item["baseline"] = round(median(others), 6)
    return evidence


def _observe_metric(
    telemetry: dict[str, Any],
    metric: str,
    *,
    normal: bool,
    critical_status: bool,
) -> _Observation | None:
    observations: list[_Observation] = []
    for raw_value, raw_device in _rows_for_metric(telemetry, metric):
        if metric == "status" or metric.endswith("_status"):
            if not isinstance(raw_value, str):
                continue
            grade = _status_grade(raw_value)
        else:
            value = _number(raw_value)
            if value is None:
                continue
            grade = _threshold_grade(metric, value)
        if metric == "status" and critical_status and grade >= 0.5:
            grade = 1.0
        if normal:
            grade = 1.0 - grade
        observations.append(_Observation(
            metric=metric,
            value=raw_value,
            grade=_clamp(grade),
            device_id=raw_device if isinstance(raw_device, str) else None,
            unit=_UNITS.get(metric),
        ))
    if not observations:
        return None
    # Normal must account for the worst reading, including a lone bad device.
    return (min if normal else max)(observations, key=lambda item: item.grade)


def _instant(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _local_scopes(
    telemetry: dict[str, Any], active: set[str], signature: list[tuple[str, float]]
):
    """Score exact, inclusive 300-second windows on devices and topology edges.

    Each active device and each edge with an active endpoint is a location.
    Sort its timed rows once, then use two pointers and per-metric monotonic
    deques to pre-score [t, t+300] and [t-300, t] at every supported row in
    linear time. Emit the top three windows per location, latest first on
    equal scores. Every pair <= 300 seconds has a candidate window; no scope
    spans more than 300 seconds. Device/service snapshots join only windows
    containing the reference instant; untimed samples join only untimed rows.
    """
    sections = ("devices", "samples", "services")
    indexed: dict[str, list[tuple[float | None, str, dict, tuple[float, ...]]]] = {}
    reference = _instant(telemetry.get("collected_at"))
    if reference is None:
        reference = max((stamp for row in telemetry.get("samples", [])
                         if (stamp := _instant(row.get("timestamp"))) is not None), default=None)
    for section in sections:
        metrics = [(i, metric) for i, (metric, _) in enumerate(signature)
                   if ("services" if _service_parts(metric) is not None else
                       "devices" if metric in _DEVICE_METRICS else "samples") == section]
        for row in telemetry.get(section, []):
            device = row.get("server_id" if section == "services" else "device_id")
            if not isinstance(device, str):
                continue
            stamp = _instant(row.get("timestamp")) if section == "samples" else reference
            grades = [0.0] * len(signature)
            for i, metric in metrics:
                grades[i] = max((grade for grade, _ in
                                 _graded_rows({section: [row]}, metric, False)), default=0.0)
            indexed.setdefault(device, []).append((stamp, section, row, tuple(grades)))
    topology = telemetry.get("topology", {})
    declared = {node.get("id") for node in topology.get("nodes", [])}
    locations = {frozenset({device}) for device in active}
    for link in topology.get("links", []):
        src, dst = link.get("src"), link.get("dst")
        if src in declared and dst in declared and (src in active or dst in active):
            locations.add(frozenset({src, dst}))
    for location in sorted(locations, key=lambda item: tuple(sorted(item))):
        timed = []
        untimed = {name: [] for name in sections}
        untimed_supported = False
        for device in sorted(location):
            for stamp, section, row, grades in indexed.get(device, []):
                if stamp is None:
                    untimed[section].append(row)
                    untimed_supported |= any(grades)
                else:
                    timed.append((stamp, section, row, grades))
        timed.sort(key=lambda item: item[0])
        anchors = []
        for stamp, _, _, grades in timed:
            if any(grades) and (not anchors or anchors[-1] != stamp):
                anchors.append(stamp)
        # Merge the two sorted streams of starts without sorting 2N windows.
        forward = backward = left = right = 0
        maxima = [deque() for _ in signature]
        top = []
        previous = None
        while forward < len(anchors) or backward < len(anchors):
            if backward == len(anchors) or (forward < len(anchors)
                    and anchors[forward] <= anchors[backward] - 300):
                start = anchors[forward]
                forward += 1
            else:
                start = anchors[backward] - 300
                backward += 1
            if start == previous:
                continue
            previous = start
            end = start + 300
            while right < len(timed) and timed[right][0] <= end:
                for i, grade in enumerate(timed[right][3]):
                    queue = maxima[i]
                    while queue and queue[-1][1] <= grade:
                        queue.pop()
                    queue.append((right, grade))
                right += 1
            while left < right and timed[left][0] < start:
                left += 1
            for queue in maxima:
                while queue and queue[0][0] < left:
                    queue.popleft()
            pre_score = sum(weight * (queue[0][1] if queue else 0.0)
                            for (_, weight), queue in zip(signature, maxima))
            top.append((pre_score, start, left, right))
            top.sort(reverse=True)
            del top[3:]
        for _, _, left, right in top:
            rows = {name: [] for name in sections}
            for _, section, row, _ in timed[left:right]:
                rows[section].append(row)
            yield {**telemetry, **rows}
        if untimed_supported:
            yield {**telemetry, **untimed}


def _hypothesis_scores(finding: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    hypotheses = finding.get("hypotheses")
    if not isinstance(hypotheses, list):
        return result
    for item in hypotheses:
        if not isinstance(item, dict) or not isinstance(item.get("cause"), str):
            continue
        score = _number(item.get("score"))
        if score is not None:
            result[item["cause"]] = _clamp(score)
    return result


def _agent_tops(findings: dict[str, dict[str, Any]]) -> dict[str, str]:
    tops: dict[str, str] = {}
    for agent, finding in findings.items():
        scores = _hypothesis_scores(finding)
        if scores:
            tops[agent] = max(scores.items(), key=lambda item: item[1])[0]
    return tops


def _agreement_factor(cause: str, tops: dict[str, str]) -> float:
    if not tops:
        return AGREEMENT_FACTOR_MIN
    # Votes are weighted by each agent's domain relevance to the cause, so a
    # low-relevance dissent costs less than a specialist's.
    relevance = AGENT_RELEVANCE.get(cause, {})
    weights = {agent: relevance.get(agent, 0.5) for agent in tops}
    total = sum(weights.values())
    if total <= 0:
        return AGREEMENT_FACTOR_MIN
    agreeing = sum(weights[agent] for agent, top in tops.items() if top == cause)
    span = AGREEMENT_FACTOR_MAX - AGREEMENT_FACTOR_MIN
    return AGREEMENT_FACTOR_MIN + span * agreeing / total


def _agent_support(
    cause: str, findings: dict[str, dict[str, Any]]
) -> float:
    relevance = AGENT_RELEVANCE[cause]
    total_weight = sum(relevance.values())
    if total_weight <= 0:
        return 0.0
    weighted = sum(
        weight * _hypothesis_scores(findings.get(agent, {})).get(cause, 0.0)
        for agent, weight in relevance.items()
    )
    return _clamp(weighted / total_weight)


def _candidate(
    cause: str,
    telemetry: dict[str, Any],
    findings: dict[str, dict[str, Any]],
    dq_factor: float,
    quality_issues: list[str],
    tops: dict[str, str],
    device_down: bool = False,
    *,
    local: bool = True,
) -> _Candidate:
    signature = SIGNATURES[cause]
    critical_status = any("severity critical" in issue for issue in quality_issues)
    observations: dict[str, _Observation] = {}
    weighted_match = 0.0
    for metric, weight in signature:
        observation = _observe_metric(
            telemetry,
            metric,
            normal=cause == "normal",
            critical_status=critical_status,
        )
        if observation is None:
            continue
        observations[metric] = observation
        weighted_match += weight * observation.grade
    # Absent metrics are not evidence of an anomaly: once "normal" has enough
    # coverage it is matched on the metrics present (coverage still scales the
    # confidence below).
    total_weight = sum(weight for _, weight in signature)
    if cause == "normal" and len(observations) / len(signature) >= MIN_SIGNATURE_COVERAGE:
        total_weight = sum(weight for metric, weight in signature if metric in observations)
    # A device reported down is decisive for hardware_failure: the metrics a dead
    # device cannot report are not evidence against it.
    if (cause == "hardware_failure" and device_down
            and len(observations) / len(signature) >= MIN_SIGNATURE_COVERAGE):
        total_weight = sum(weight for metric, weight in signature if metric in observations)
    match = _clamp(weighted_match / total_weight) if total_weight else 0.0
    if cause == "normal" and observations:
        # A device is only as healthy as its worst metric; averages can hide
        # a warning behind its own healthy metrics or the rest of the fleet.
        match = min(match, min(observation.grade for observation in observations.values()))
    if cause == "normal" and (device_down or critical_status or any(
        (bad := _observe_metric(telemetry, metric, normal=False,
                                critical_status=critical_status)) is not None
        and bad.grade >= 1.0
        for metric, _ in signature
    )):
        match = 0.0  # normal is ineligible while any device is down or any metric is critical
    coverage = len(observations) / len(signature) if signature else 0.0
    agreement = _agreement_factor(cause, tops)
    confidence = _clamp(
        match * (0.5 + 0.5 * coverage) * dq_factor * agreement
    )
    if cause == "congestion" and (
        observations.get("link_utilization_pct") is None
        or observations["link_utilization_pct"].grade <= 0
        or sum(item.grade > 0 for item in observations.values()) < 2
    ):
        confidence = min(confidence, INSUFFICIENT_CONFIDENCE_THRESHOLD - 0.001)
    limitations: list[str] = []
    if cause == "normal":
        warning_devices = sorted({
            device for metric in [*(metric for metric, _ in signature), "status"]
            for value, device in _rows_for_metric(telemetry, metric)
            if device is not None and (
                _status_grade(value) > 0 if metric == "status" else
                (number := _number(value)) is not None
                and (number <= THRESHOLDS[metric]["warn"] if metric == "rssi_dbm"
                     else number >= THRESHOLDS[metric]["warn"])
            )
        })
        if warning_devices:
            confidence = min(confidence, 0.749)
            limitations.append("warning-grade devices: " + ", ".join(warning_devices))
    support = _agent_support(cause, findings)
    rank = _clamp(
        confidence * (1.0 - AGENT_SUPPORT_RANK_WEIGHT)
        + support * AGENT_SUPPORT_RANK_WEIGHT
    )
    if cause == "normal" and match == 0.0 and observations:
        rank = 0.0
    candidate = _Candidate(
        cause=cause,
        match=match,
        coverage=coverage,
        confidence=confidence,
        agreement=agreement,
        support=support,
        rank=rank,
        observations=observations,
        limitations=limitations,
    )
    if local and cause != "normal" and any(item.grade > 0 for item in observations.values()):
        active = {
            device for metric, _ in signature
            for grade, device in _graded_rows(telemetry, metric, critical_status)
            if grade > 0 and device is not None
        }
        best = best_key = None
        for scope in _local_scopes(telemetry, active, signature):
            item = _candidate(cause, scope, findings, dq_factor, quality_issues, tops,
                              _device_down(scope), local=False)
            item.telemetry = scope
            # On equal scores prefer the edge scope with more anomalous devices,
            # so a linked neighbour with the same fault stays in affected_devices.
            spread = len({device for metric, _ in signature
                          for grade, device in _graded_rows(scope, metric, critical_status)
                          if grade > 0 and device is not None})
            key = (item.rank, item.coverage, spread)
            if best is None or key > best_key:
                best, best_key = item, key
        if best is not None:
            candidate = best
        global_support = sum(item.grade > 0 for item in observations.values())
        local_support = sum(item.grade > 0 for item in candidate.observations.values())
        global_signature = global_support >= 2 and (
            cause != "congestion" or (observations.get("link_utilization_pct") is not None
                                     and observations["link_utilization_pct"].grade > 0)
        )
        if global_signature and (
            local_support < 2 or (cause == "congestion"
                                 and _grade(candidate, "link_utilization_pct") <= 0)
        ):
            candidate.confidence = min(candidate.confidence, INSUFFICIENT_CONFIDENCE_THRESHOLD - 0.001)
            candidate.rank = min(candidate.rank, candidate.confidence)
            candidate.limitations.append(f"{cause.replace('_', ' ')}: evidence from unrelated devices/times")
    return candidate


def _grade(candidate: _Candidate, metric: str) -> float:
    observation = candidate.observations.get(metric)
    return observation.grade if observation is not None else 0.0


def _select_winner(
    candidates: list[_Candidate], quality_issues: list[str], device_down: bool = False
) -> tuple[_Candidate, str | None]:
    by_cause = {candidate.cause: candidate for candidate in candidates}
    for service in ("dns", "dhcp"):
        candidate = by_cause[f"{service}_failure"]
        service_down = _grade(candidate, f"{service}_status") >= 1.0
        service_failing = max(
            _grade(candidate, f"{service}_success_rate_pct"),
            _grade(candidate, f"{service}_response_ms"),
        ) >= 0.5
        if service_down and service_failing:
            return candidate, (
                f"{service.upper()} service failure outranks server hardware status"
            )

    hardware = by_cause["hardware_failure"]
    if device_down:
        interface = by_cause["interface_fault"]
        errors = _grade(interface, "interface_errors")
        if errors >= 0.5 and errors > _grade(hardware, "cpu_pct"):
            return interface, "a device is down and interface errors dominate"
        return hardware, "a device reported down"
    critical = any("severity critical" in issue for issue in quality_issues)
    if critical and max(
        _grade(hardware, "cpu_pct"), _grade(hardware, "interface_errors")
    ) >= 0.5:
        return hardware, "critical device status resolves competing hardware signals"
    if (
        _grade(hardware, "cpu_pct") >= 1.0
        and _grade(hardware, "interface_errors") >= 1.0
        and max(_grade(hardware, "packet_loss_pct"), _grade(hardware, "latency_ms")) >= 1.0
    ):
        return hardware, "critical resource, interface and path metrics together indicate hardware failure"

    congestion = by_cause["congestion"]
    if (
        _grade(congestion, "link_utilization_pct") >= 0.75
        and _grade(congestion, "queue_drops") >= 0.5
    ):
        return congestion, (
            "utilization plus queue drops resolve the conflict as congestion"
        )
    latency = by_cause["latency_degradation"]
    if (
        latency.match >= 0.55
        and _grade(latency, "latency_ms") >= 0.5
        and _grade(congestion, "link_utilization_pct") < 0.75
        and all(latency.confidence >= item.confidence
                for item in candidates if item is not latency and item.cause != "normal")
    ):
        return latency, "latency and jitter dominate while utilization is below critical"
    return max(candidates, key=lambda item: (item.rank, item.cause)), None


def _conflict_messages(
    tops: dict[str, str], winner: _Candidate, runner: _Candidate | None
) -> list[str]:
    conflicts: list[str] = []
    if len(set(tops.values())) > 1:
        summary = ", ".join(
            f"{agent}={cause}" for agent, cause in sorted(tops.items())
        )
        conflicts.append(f"Agents disagree on their top hypothesis: {summary}")
    if not winner.observations and (runner is None or not runner.observations):
        conflicts.append("no usable telemetry samples supplied")
    elif runner is not None and abs(winner.rank - runner.rank) < QUERY_MARGIN_THRESHOLD:
        conflicts.append(
            f"Leading hypotheses are close: {winner.cause}={winner.confidence:.3f}, "
            f"{runner.cause}={runner.confidence:.3f}"
        )
    return conflicts


def _score(context: _RunContext) -> _ScoreResult:
    dq_factor, quality_issues, _ = _quality_details(context.findings)
    tops = _agent_tops(context.findings)
    device_down = _device_down(context.telemetry)
    candidates = [
        _candidate(
            cause,
            context.telemetry,
            context.findings,
            dq_factor,
            quality_issues,
            tops,
            device_down,
        )
        for cause in SIGNATURES
    ]
    winner, resolution = _select_winner(candidates, quality_issues, device_down)
    remaining = sorted(
        (candidate for candidate in candidates if candidate is not winner),
        key=lambda candidate: (-candidate.rank, candidate.cause),
    )
    ordered = [winner, *remaining]
    runner = remaining[0] if remaining else None
    margin = winner.rank - runner.rank if runner is not None else winner.rank
    conflicts = list(dict.fromkeys([
        *_conflict_messages(tops, winner, runner),
    ]))
    if resolution is not None:
        conflicts.append(f"Conflict resolved by domain rule: {resolution}")
    higher = [item for item in remaining if item.confidence >= winner.confidence > 0.0]
    if higher:
        raw = ", ".join(f"{item.cause}={item.confidence:.3f}" for item in higher)
        how = "domain rule" if resolution is not None else "agent-weighted rank"
        conflicts.append(
            f"selected by {how} over higher raw score: {winner.cause}="
            f"{winner.confidence:.3f} vs {raw}"
        )
    unresolved = (
        resolution is None
        and runner is not None
        and winner.cause != "normal"
        and runner.cause != "normal"
        and len(set(tops.values())) > 1
        and abs(margin) < QUERY_MARGIN_THRESHOLD
        and winner.confidence >= QUERY_CONFIDENCE_THRESHOLD
        and runner.confidence >= QUERY_CONFIDENCE_THRESHOLD
    )
    return _ScoreResult(
        winner=winner,
        ordered=ordered,
        margin=margin,
        conflicts=conflicts,
        resolution=resolution,
        unresolved_conflict=unresolved,
    )


def _historical_limitations(
    telemetry: dict[str, Any], winner: _Candidate, affected: list[str]
) -> list[str]:
    """Qualify past anomalies when the latest accepted sample has recovered."""
    if winner.cause == "normal" or not affected:
        return []
    signature = SIGNATURES[winner.cause]
    reference = _instant(telemetry.get("collected_at"))
    if reference is None:
        reference = max((stamp for row in telemetry.get("samples", [])
                         if (stamp := _instant(row.get("timestamp"))) is not None), default=None)
    if reference is None:
        return []
    affected_set = set(affected)
    newest_support: dict[str, float] = {}
    scope = winner.telemetry or telemetry
    for section in ("devices", "samples", "services"):
        metrics = [metric for metric, _ in signature
                   if ("services" if _service_parts(metric) is not None else
                       "devices" if metric in _DEVICE_METRICS else "samples") == section]
        for row in scope.get(section, []):
            device = row.get("server_id" if section == "services" else "device_id")
            stamp = _instant(row.get("timestamp")) if section == "samples" else reference
            if device not in affected_set or stamp is None:
                continue
            if any(grade > 0 for metric in metrics
                   for grade, _ in _graded_rows({section: [row]}, metric, False)):
                newest_support[device] = max(newest_support.get(device, stamp), stamp)
    latest: dict[str, tuple[float, list[dict]]] = {}
    for row in telemetry.get("samples", []):
        device = row.get("device_id")
        stamp = _instant(row.get("timestamp"))
        if (device not in newest_support or stamp is None
                or not reference - 3600 <= stamp <= reference + 300):
            continue
        if device not in latest or stamp > latest[device][0]:
            latest[device] = (stamp, [row])
        elif stamp == latest[device][0]:
            latest[device][1].append(row)
    sample_metrics = [metric for metric, _ in signature
                      if metric not in _DEVICE_METRICS and _service_parts(metric) is None]
    limitations = []
    for device in affected:
        if device not in latest:
            continue
        stamp, rows = latest[device]
        grades = [grade for metric in sample_metrics
                  for grade, _ in _graded_rows({"samples": rows}, metric, False)]
        if stamp > newest_support[device] and grades and not any(grades):
            anomaly = datetime.fromtimestamp(newest_support[device], timezone.utc).isoformat().replace("+00:00", "Z")
            recovered = datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z")
            limitations.append(
                f"historical: {device} anomaly at {anomaly}; latest sample "
                f"{recovered} is healthy (recovered)"
            )
    return limitations


def _evidence_key(payload: dict[str, Any]) -> tuple[str, ...]:
    return tuple(f"{key}={payload.get(key)!r}" for key in sorted(payload))


def _merge_finding(
    existing: dict[str, Any] | None, incoming: Finding
) -> dict[str, Any]:
    if existing is None:
        return incoming.model_dump(mode="json")
    base = Finding.model_validate(existing)
    scores = {item.cause: item.score for item in base.hypotheses}
    for item in incoming.hypotheses:
        scores[item.cause] = max(scores.get(item.cause, 0.0), item.score)
    evidence = [item.model_dump(mode="json") for item in base.evidence]
    seen = {_evidence_key(item) for item in evidence}
    for item in incoming.evidence:
        payload = item.model_dump(mode="json")
        key = _evidence_key(payload)
        if key not in seen:
            seen.add(key)
            evidence.append(payload)
    return {
        "agent": base.agent,
        "hypotheses": [
            {"cause": cause, "score": score}
            for cause, score in sorted(
                scores.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "affected_devices": list(dict.fromkeys([
            *base.affected_devices, *incoming.affected_devices
        ])),
        "evidence": evidence,
        "data_quality": (
            base.data_quality.model_dump(mode="json")
            if base.data_quality is not None
            else (
                incoming.data_quality.model_dump(mode="json")
                if incoming.data_quality is not None
                else None
            )
        ),
        "notes": list(dict.fromkeys([*base.notes, *incoming.notes])),
    }


def _confidence_category(confidence: float) -> str:
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.50:
        return "medium"
    if confidence >= 0.30:
        return "low"
    return "insufficient"


class CorrelatorAgent(Agent):
    """Combine specialist findings and run bounded adaptive follow-ups."""

    name = "correlator"

    def __init__(self) -> None:
        self._runs: dict[str, _RunContext] = {}
        self._query_runs: dict[str, str] = {}

    async def handle(self, envelope: Envelope) -> list[Envelope]:
        try:
            if envelope.type == MsgType.TASK:
                return self._handle_task(envelope)
            if envelope.type in (MsgType.RESPONSE, MsgType.ERROR):
                return self._handle_feedback(envelope)
            return [self._error(
                envelope,
                "correlator accepts TASK, RESPONSE, and ERROR envelopes only",
            )]
        except Exception as exc:  # Protocol boundary: never raise into the bus.
            return [self._error(envelope, f"unable to correlate findings: {exc}")]

    def _handle_task(self, envelope: Envelope) -> list[Envelope]:
        raw_findings = envelope.payload.get("findings")
        telemetry = envelope.payload.get("telemetry")
        if not isinstance(raw_findings, list):
            return [self._error(envelope, "TASK payload findings must be a list")]
        if not isinstance(telemetry, dict):
            return [self._error(envelope, "TASK payload telemetry must be an object")]

        findings: dict[str, dict[str, Any]] = {}
        for index, payload in enumerate(raw_findings):
            if not isinstance(payload, dict):
                return [self._error(
                    envelope, f"TASK finding at index {index} must be an object"
                )]
            try:
                finding = Finding.model_validate(payload)
            except Exception as exc:
                return [self._error(
                    envelope, f"invalid TASK finding at index {index}: {exc}"
                )]
            findings[finding.agent] = finding.model_dump(mode="json")

        raw_round = envelope.payload.get("round", 0)
        if (
            isinstance(raw_round, bool)
            or not isinstance(raw_round, int)
            or raw_round < 0
        ):
            return [self._error(
                envelope, "TASK payload round must be a non-negative integer"
            )]
        raw_limitations = envelope.payload.get("limitations", [])
        if not isinstance(raw_limitations, list):
            return [self._error(
                envelope, "TASK payload limitations must be a list"
            )]

        run_id = envelope.correlation_id or envelope.msg_id
        context = _RunContext(
            run_id=run_id,
            requester=envelope.sender,
            findings=findings,
            telemetry=telemetry,
            limitations=[item for item in raw_limitations if isinstance(item, str)],
            round=raw_round,
        )
        self._runs[run_id] = context
        return self._next(context)

    def _handle_feedback(self, envelope: Envelope) -> list[Envelope]:
        lookup = envelope.correlation_id or ""
        run_id = self._query_runs.pop(lookup, lookup)
        context = self._runs.get(run_id)
        if context is None:
            return [self._error(
                envelope, "RESPONSE does not match an active correlation run"
            )]

        context.round += 1
        if envelope.type == MsgType.ERROR:
            message = str(
                envelope.payload.get("message", "query target returned an error")
            )
            context.limitations.append(
                f"{envelope.sender} query failed: {message}"
            )
        else:
            self._merge_response(context, envelope)
        return self._next(context)

    def _merge_response(self, context: _RunContext, envelope: Envelope) -> None:
        try:
            finding = Finding.model_validate(envelope.payload)
        except Exception:
            finding = None
        if finding is not None:
            context.findings[finding.agent] = _merge_finding(
                context.findings.get(finding.agent), finding
            )
            return

        if envelope.payload.get("action") != "upstream_neighbors":
            raise ValueError(
                "RESPONSE payload is neither a Finding nor a supported query answer"
            )
        agent = envelope.sender
        existing = context.findings.get(agent)
        if existing is None:
            existing = Finding(
                agent=agent,
                hypotheses=[],
                affected_devices=[],
                evidence=[],
                notes=[],
            ).model_dump(mode="json")
        neighbors = envelope.payload.get("upstream_neighbors", [])
        neighbor_text = ", ".join(str(item) for item in neighbors) or "none"
        device_id = envelope.payload.get("device_id")
        incoming = Finding(
            agent=agent,
            hypotheses=[],
            affected_devices=[device_id] if isinstance(device_id, str) else [],
            evidence=[Evidence(
                metric="upstream_neighbors",
                value=neighbor_text,
                device_id=device_id if isinstance(device_id, str) else None,
                interpretation=f"queried upstream neighbors: {neighbor_text}",
            )],
            notes=[
                note
                for note in envelope.payload.get("notes", [])
                if isinstance(note, str)
            ],
        )
        context.findings[agent] = _merge_finding(existing, incoming)

    def _next(self, context: _RunContext) -> list[Envelope]:
        result = _score(context)
        needs_query = (
            result.winner.confidence < QUERY_CONFIDENCE_THRESHOLD
            or result.margin < QUERY_MARGIN_THRESHOLD
        )
        if needs_query and context.round < MAX_CORRELATION_ROUNDS:
            query = self._make_query(context, result)
            if query is not None:
                self._query_runs[query.msg_id] = context.run_id
                return [query]
        return [self._final_finding(context, result)]

    def _make_query(
        self, context: _RunContext, result: _ScoreResult
    ) -> Envelope | None:
        candidates = [result.winner]
        if len(result.ordered) > 1:
            candidates.append(result.ordered[1])
        options: list[tuple[str, str, dict[str, Any]]] = []
        for candidate in candidates:
            device_id = self._candidate_device(candidate) or self._first_device(
                context
            )
            if candidate.cause in {
                "weak_wifi", "congestion", "latency_degradation"
            }:
                if device_id is not None:
                    options.append((
                        "performance", "device_detail", {"device_id": device_id}
                    ))
                options.append((
                    "performance", "widen_window", {"minutes": 30}
                ))
            elif candidate.cause in {"device_overload", "hardware_failure"}:
                if device_id is not None:
                    options.append((
                        "infrastructure", "device_detail", {"device_id": device_id}
                    ))
                    options.append((
                        "connectivity", "upstream_neighbors", {"device_id": device_id}
                    ))
            elif candidate.cause == "interface_fault":
                if device_id is not None:
                    options.append((
                        "infrastructure", "device_detail", {"device_id": device_id}
                    ))
                    options.append((
                        "connectivity", "upstream_neighbors", {"device_id": device_id}
                    ))
            elif candidate.cause in {"dns_failure", "dhcp_failure"}:
                if device_id is not None:
                    options.append((
                        "connectivity", "upstream_neighbors", {"device_id": device_id}
                    ))
            else:
                options.append((
                    "performance", "widen_window", {"minutes": 30}
                ))
                if device_id is not None:
                    options.append((
                        "infrastructure", "device_detail", {"device_id": device_id}
                    ))

        reason = (
            f"confidence={result.winner.confidence:.3f}, coverage="
            f"{result.winner.coverage:.3f}, top-two margin={result.margin:.3f}"
        )
        for target, action, params in options:
            key = (target, action, repr(sorted(params.items())))
            if key in context.queries_sent:
                continue
            context.queries_sent.add(key)
            query_payload = Query(
                target_agent=target,
                action=action,
                params=params,
                reason=reason,
            ).model_dump(mode="json")
            return Envelope(
                type=MsgType.QUERY,
                sender=self.name,
                recipient=target,
                correlation_id=context.run_id,
                payload=query_payload,
            )
        return None

    @staticmethod
    def _candidate_device(candidate: _Candidate) -> str | None:
        graded = sorted(
            candidate.observations.values(),
            key=lambda item: item.grade,
            reverse=True,
        )
        return next((item.device_id for item in graded if item.device_id), None)

    @staticmethod
    def _first_device(context: _RunContext) -> str | None:
        devices = context.telemetry.get("devices")
        if not isinstance(devices, list):
            return None
        return next((
            row.get("device_id")
            for row in devices
            if isinstance(row, dict) and isinstance(row.get("device_id"), str)
        ), None)

    def _final_finding(
        self, context: _RunContext, result: _ScoreResult
    ) -> Envelope:
        winner = result.winner
        confidence = (
            min(winner.confidence, 0.299)
            if result.unresolved_conflict
            else winner.confidence
        )
        insufficient = (
            confidence < INSUFFICIENT_CONFIDENCE_THRESHOLD
            or winner.coverage < MIN_SIGNATURE_COVERAGE
            or result.unresolved_conflict
        )
        cause = "insufficient_evidence" if insufficient else winner.cause
        status = "insufficient_evidence" if insufficient else "diagnosed"
        # Domain-rule / rank notes describe the winner; drop them when it is not reported.
        conflicts = [
            item for item in result.conflicts
            if not (insufficient and item.startswith(
                ("Conflict resolved by domain rule", "selected by")))
        ]
        evidence, sources = self._supporting_evidence(context, winner)
        affected = self._affected_devices(context, winner)
        evidence = _enrich_evidence(evidence, context.telemetry, affected)
        alternatives = []
        for candidate in (result.ordered if result.unresolved_conflict else result.ordered[1:]):
            conflict_alternative = (
                result.unresolved_conflict
                and abs(winner.confidence - candidate.confidence) <= ALTERNATIVE_CONFIDENCE_WINDOW
            )
            overruled = (
                not insufficient and result.resolution is not None
                and candidate.confidence >= confidence
            )
            if not conflict_alternative and not overruled and not (
                candidate.confidence < confidence
                and confidence - candidate.confidence <= ALTERNATIVE_CONFIDENCE_WINDOW
            ):
                continue
            alternative = {
                "cause": candidate.cause,
                "confidence": round(candidate.confidence, 6),
            }
            if conflict_alternative:
                alternative["overruled_by"] = "unresolved agent conflict"
            elif overruled:
                alternative["overruled_by"] = result.resolution
            alternatives.append(alternative)
        _, _, quality_payload = _quality_details(context.findings)
        visible_causes = {winner.cause, *(item["cause"] for item in alternatives)}
        if len(result.ordered) > 1:
            visible_causes.add(result.ordered[1].cause)
        candidate_limitations = [
            limitation for candidate in result.ordered
            if candidate.cause in visible_causes
            for limitation in candidate.limitations
        ]
        candidate_limitations.extend(_historical_limitations(context.telemetry, winner, affected))
        conflicts = list(dict.fromkeys([*conflicts, *candidate_limitations]))
        finding = Finding(
            agent=self.name,
            hypotheses=[
                Hypothesis(
                    cause=candidate.cause,
                    score=round(candidate.confidence, 6),
                )
                for candidate in result.ordered
            ],
            affected_devices=affected,
            evidence=[Evidence.model_validate(item) for item in evidence],
            data_quality=(
                DataQuality.model_validate(quality_payload)
                if quality_payload is not None
                else None
            ),
            notes=list(dict.fromkeys([
                *context.limitations,
                *candidate_limitations,
                f"correlation rounds completed: {context.round}",
            ])),
        )
        payload = finding.model_dump(mode="json")
        payload["diagnosis"] = {
            "status": status,
            "cause": cause,
            "confidence": round(confidence, 6),
            "confidence_category": _confidence_category(confidence),
            "affected_devices": affected,
            "alternatives": alternatives,
            "evidence": evidence,
            "evidence_sources": sources,
            "conflicts": conflicts,
            "coverage": round(winner.coverage, 6),
            "agreement": round(winner.agreement, 6),
            "match": round(winner.match, 6),
        }
        return Envelope(
            type=MsgType.FINDING,
            sender=self.name,
            recipient=context.requester,
            correlation_id=context.run_id,
            payload=payload,
        )

    def _supporting_evidence(
        self, context: _RunContext, winner: _Candidate
    ) -> tuple[list[dict[str, Any]], list[str]]:
        signature = dict(SIGNATURES[winner.cause])
        scoped_values = ({
            metric: set(_rows_for_metric(winner.telemetry, metric))
            for metric in signature
        } if winner.telemetry is not None else None)
        scoped_times = ({
            (row.get("device_id"), _instant(row.get("timestamp")))
            for row in winner.telemetry.get("samples", [])
        } if winner.telemetry is not None else None)
        ranked: list[tuple[float, dict[str, Any], str | None]] = []
        seen: set[tuple[str, str | None, Any]] = set()
        for agent, finding in context.findings.items():
            raw_evidence = finding.get("evidence")
            if not isinstance(raw_evidence, list):
                continue
            for payload in raw_evidence:
                if not isinstance(payload, dict):
                    continue
                metric = payload.get("metric")
                if not isinstance(metric, str) or metric not in signature:
                    continue
                key = (metric, payload.get("device_id"), payload.get("value"))
                if scoped_values is not None:
                    if (payload.get("value"), payload.get("device_id")) not in scoped_values[metric]:
                        continue
                    if (metric not in _DEVICE_METRICS and _service_parts(metric) is None
                            and payload.get("timestamp") is not None
                            and (payload.get("device_id"), _instant(payload["timestamp"])) not in scoped_times):
                        continue
                if key in seen:
                    continue
                seen.add(key)
                observation = winner.observations.get(metric)
                grade = observation.grade if observation is not None else 0.0
                ranked.append((signature[metric] * grade, payload, agent))

        for metric, observation in winner.observations.items():
            if (metric, observation.device_id, observation.value) in seen:
                continue
            limits = THRESHOLDS.get(metric)
            service_parts = _service_parts(metric)
            if service_parts is not None:
                limits = SERVICE_THRESHOLDS.get(service_parts[1])
            payload = Evidence(
                metric=metric,
                value=observation.value,
                unit=observation.unit,
                threshold=limits.get("warn") if limits else None,
                device_id=observation.device_id,
                interpretation=(
                    f"raw telemetry contributes grade {observation.grade:.3f} "
                    f"to {winner.cause}"
                ),
            ).model_dump(mode="json")
            ranked.append((signature[metric] * observation.grade, payload, None))

        ranked.sort(key=lambda item: item[0], reverse=True)
        selected = ranked[:MAX_SUPPORTING_EVIDENCE]
        sources = sorted({
            agent for _, _, agent in selected if agent is not None
        })
        if not sources:
            tops = _agent_tops(context.findings)
            sources = sorted(
                agent for agent, cause in tops.items() if cause == winner.cause
            )
        return [payload for _, payload, _ in selected], sources

    @staticmethod
    def _affected_devices(
        context: _RunContext, winner: _Candidate
    ) -> list[str]:
        """Devices with a non-zero grade on the winning signature's metrics (worst first),
        plus the topology-derived common upstream device when connectivity found one."""
        if winner.cause == "normal":
            return []
        _, quality_issues, _ = _quality_details(context.findings)
        critical_status = any("severity critical" in issue for issue in quality_issues)
        graded: dict[str, float] = {}
        for metric, _ in SIGNATURES[winner.cause]:
            for grade, device_id in _graded_rows(winner.telemetry or context.telemetry, metric, critical_status):
                if grade > 0 and device_id is not None:
                    graded[device_id] = max(graded.get(device_id, 0.0), grade)
        devices = [d for d, _ in sorted(graded.items(), key=lambda item: -item[1])]
        connectivity = context.findings.get("connectivity", {})
        for item in connectivity.get("evidence", []) if isinstance(connectivity, dict) else []:
            if (isinstance(item, dict) and item.get("metric") == "common_upstream"
                    and isinstance(item.get("device_id"), str)
                    and devices and item["device_id"] not in devices):
                devices.append(item["device_id"])
        return devices

    def _error(self, envelope: Envelope, message: str) -> Envelope:
        return Envelope(
            type=MsgType.ERROR,
            sender=self.name,
            recipient=envelope.sender,
            correlation_id=envelope.correlation_id or envelope.msg_id,
            payload={
                "code": "CORRELATOR_ERROR",
                "message": message,
                "recoverable": True,
            },
        )
