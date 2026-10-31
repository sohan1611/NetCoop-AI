"""Presentation payload for the console UI, derived from a finished run (A2)."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from netcoop.agents.correlator import _UNITS, _graded_rows, _number, _threshold_grade
from netcoop.config import SIGNATURES
from netcoop.state import RunState

AGENTS = ("normalizer", "performance", "infrastructure", "connectivity", "correlator")
ANALYSIS = ("performance", "infrastructure", "connectivity")
MAX_MAPPINGS = 50
MAX_DEVICES = 200
MAX_SERIES = 200
_DEVICE_METRICS = ("cpu_pct", "mem_pct", "interface_errors", "queue_drops")
_SAMPLE_METRICS = ("latency_ms", "jitter_ms", "packet_loss_pct", "link_utilization_pct",
                   "rssi_dbm", "retransmission_pct")


def _pct(value: float) -> str:
    # Round half up like the console's Math.round, so a card never shows 93% beside (92%).
    return f"{math.floor(value * 100 + 0.5)}%"


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _window(samples: list[dict[str, Any]]) -> dict[str, Any]:
    stamps = [(p, row["timestamp"]) for row in samples
              if (p := _parse(row.get("timestamp"))) is not None]
    if not stamps:
        return {"first": None, "last": None, "seconds": 0}
    first, last = min(stamps, key=lambda i: i[0]), max(stamps, key=lambda i: i[0])
    return {"first": first[1], "last": last[1], "seconds": int((last[0] - first[0]).total_seconds())}


def _severity(grade: float) -> str:
    return "critical" if grade >= 1.0 else "warning" if grade > 0 else "ok"


def _devices(telemetry: dict[str, Any], affected: list[str]) -> list[dict[str, Any]]:
    nodes = {n["id"]: n for n in (telemetry.get("topology") or {}).get("nodes", [])
             if isinstance(n, dict) and isinstance(n.get("id"), str)}
    rows: dict[str, dict[str, Any]] = {}
    for row in telemetry.get("devices") or []:
        if isinstance(row, dict) and isinstance(row.get("device_id"), str):
            rows.setdefault(row["device_id"], {}).update(row)
    worst: dict[str, tuple[float, str | None, Any]] = {}

    def consider(device_id: str, grade: float, metric: str, value: Any) -> None:
        current = worst.get(device_id)
        if current is None or grade > current[0]:
            worst[device_id] = (grade, metric, value)

    for row in telemetry.get("devices") or []:
        if not isinstance(row, dict) or not isinstance(row.get("device_id"), str):
            continue
        for metric in _DEVICE_METRICS:
            number = _number(row.get(metric))
            if number is not None:
                consider(row["device_id"], _threshold_grade(metric, number), metric, number)
        status = row.get("status")
        if isinstance(status, str) and status != "up":
            consider(row["device_id"], 1.0 if status == "down" else 0.5, "status", status)
    for row in telemetry.get("samples") or []:
        if not isinstance(row, dict) or not isinstance(row.get("device_id"), str):
            continue
        for metric in _SAMPLE_METRICS:
            number = _number(row.get(metric))
            if number is not None:
                consider(row["device_id"], _threshold_grade(metric, number), metric, number)
    ids = list(dict.fromkeys([*affected, *rows, *nodes, *worst]))
    ids.sort(key=lambda d: (d not in affected, -worst.get(d, (0.0,))[0]))
    result = []
    for device_id in ids[:MAX_DEVICES]:
        grade, metric, value = worst.get(device_id, (0.0, None, None))
        node = nodes.get(device_id, {})
        result.append({
            "device_id": device_id,
            "type": node.get("type") or rows.get(device_id, {}).get("type"),
            "role": node.get("role"), "location": node.get("location"),
            "status": rows.get(device_id, {}).get("status"),
            "worst_metric": metric, "worst_value": value,
            "unit": _UNITS.get(metric) if metric else None,
            "severity": _severity(grade),
        })
    return result


def _latency_series(telemetry: dict[str, Any], affected: list[str]) -> list[dict[str, Any]]:
    if not affected:
        return []
    points = []
    for row in telemetry.get("samples") or []:
        if not isinstance(row, dict) or row.get("device_id") != affected[0]:
            continue
        value, stamp = _number(row.get("latency_ms")), _parse(row.get("timestamp"))
        if value is not None and stamp is not None:
            points.append((stamp, row["timestamp"], value))
    points.sort(key=lambda p: p[0])
    return [{"t": raw, "value": value} for _, raw, value in points[-MAX_SERIES:]]


def _agent_view(name: str, state: RunState, diagnosis: dict[str, Any], failed: set[str]) -> dict[str, Any]:
    finding = state.findings.get(name)
    base = {"name": name, "status": "failed", "top_hypothesis": None, "score": None,
            "evidence_count": 0, "summary": "", "stats": {}}
    if name in failed or not isinstance(finding, dict) and name != "correlator":
        base["summary"] = next((i for i in state.limitations if i.startswith(f"{name} unavailable")),
                               f"{name} produced no finding")
        return base
    if name == "normalizer":
        quality = (finding.get("data_quality") or {}).get("score")
        mapped = len(finding.get("mappings") or [])
        formats = ", ".join(finding.get("formats") or [])
        telemetry = state.telemetry or {}
        base.update(status="complete" if quality is None or quality >= 0.5 else "flagged",
                    summary=f"Normalized {formats or 'input'} telemetry"
                            + (f"; data quality {_pct(quality)}" if quality is not None else ""),
                    stats={"Ingested": f"{len(telemetry.get('samples') or [])} samples",
                           "Mapped": f"{mapped} fields"})
        return base
    if name == "correlator":
        cause, confidence = diagnosis.get("cause"), diagnosis.get("confidence")
        flagged = cause not in (None, "normal")
        base.update(
            status=("alert" if flagged and diagnosis.get("status") == "diagnosed"
                    and (confidence or 0) >= 0.75 else "flagged" if flagged else "complete"),
            top_hypothesis=cause, score=confidence,
            evidence_count=len(diagnosis.get("evidence") or []),
            summary=f"Concluded {cause} at {_pct(confidence or 0)} after {state.rounds} query round{'' if state.rounds == 1 else 's'}",
            stats={"Rounds": str(state.rounds), "Conflicts": str(len(diagnosis.get("conflicts") or []))})
        return base
    hypotheses = [h for h in finding.get("hypotheses") or [] if isinstance(h, dict)]
    evidence = [e for e in finding.get("evidence") or [] if isinstance(e, dict)]
    stats = {"Evidence": str(len(evidence)),
             "Flagged": f"{len(finding.get('affected_devices') or [])} devices"}
    notes = [n for n in finding.get("notes") or [] if isinstance(n, str)]
    base.update(evidence_count=len(evidence), stats=stats)
    if not hypotheses:
        base.update(status="abstained", summary=notes[0] if notes else "No opinion: insufficient inputs")
        return base
    top = max(hypotheses, key=lambda h: h.get("score", 0))
    critical = any((v := _number(e.get("value"))) is not None
                   and _threshold_grade(str(e.get("metric")), v) >= 1.0 for e in evidence)
    flagged = top.get("cause") != "normal"
    base.update(status="alert" if flagged and critical else "flagged" if flagged else "complete",
                top_hypothesis=top.get("cause"), score=top.get("score"),
                summary=f"Top hypothesis {top.get('cause')} ({_pct(top.get('score', 0))})"
                        + (f". {notes[0]}" if notes else ""))
    return base


def _rationale(cause: str, telemetry: dict[str, Any]) -> str:
    best: tuple[float, str, str | None] | None = None
    for metric, _ in SIGNATURES.get(cause, []):
        for grade, device in _graded_rows(telemetry, metric, False):
            if best is None or grade > best[0]:
                best = (grade, metric, device)
    if best is None or best[0] <= 0:
        return "No signature metric is out of range."
    where = f" on {best[2]}" if best[2] else ""
    return f"Strongest matching signal: {best[1]}{where} (severity {best[0]:.2f})."


def build_details(state: RunState, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble the A2 payload. Works on partial/error runs (lists come back empty)."""
    telemetry = state.telemetry or {}
    correlator = state.findings.get("correlator") if isinstance(state.findings.get("correlator"), dict) else {}
    diagnosis = correlator.get("diagnosis") or {}
    report = (state.findings.get("reporter") or {}).get("report") or {}
    normalizer = state.findings.get("normalizer") if isinstance(state.findings.get("normalizer"), dict) else {}
    affected = [d for d in diagnosis.get("affected_devices") or [] if isinstance(d, str)]
    failed = {n for n in AGENTS if any(i.startswith(f"{n} unavailable") for i in state.limitations)}
    agents = [_agent_view(name, state, diagnosis, failed) for name in AGENTS]

    cause = diagnosis.get("cause")
    engaged = [a for a in agents if a["status"] not in ("failed", "abstained")]
    in_accord = [a for a in engaged
                 if a["name"] in ("normalizer", "correlator") or a["top_hypothesis"] == cause]
    consensus = {"engaged": len(engaged), "total": len(agents), "in_accord": len(in_accord),
                 "synchronized": bool(engaged) and len(in_accord) == len(engaged)}

    wanted = set(affected)
    evidence = diagnosis.get("evidence") or []
    cited = {e.get("metric") for e in evidence if isinstance(e, dict)}
    mappings = sorted((m for m in normalizer.get("mappings") or [] if isinstance(m, dict)),
                      key=lambda m: (m.get("device_id") not in wanted,
                                     m.get("metric") not in cited))[:MAX_MAPPINGS]
    stamps = [m.get("timestamp") for m in (messages[:1] + messages[-1:]) if isinstance(m, dict)]
    return {
        "incident_id": report.get("incident_id") or telemetry.get("incident_id"),
        "status": report.get("status") or ("error" if state.stage == "error" else diagnosis.get("status")),
        "started_at": stamps[0] if stamps else None,
        "finished_at": stamps[-1] if stamps else None,
        "rounds": state.rounds,
        "window": _window(telemetry.get("samples") or []),
        "formats": list(normalizer.get("formats") or []),
        "validation_issues": len((normalizer.get("data_quality") or {}).get("issues") or []),
        "mappings": mappings,
        "agents": agents,
        "consensus": consensus,
        "devices": _devices(telemetry, affected),
        "latency_series": _latency_series(telemetry, affected),
        "alternatives": [
            {"cause": a["cause"], "confidence": a["confidence"],
             "rationale": _rationale(a["cause"], telemetry),
             **({"overruled_by": a["overruled_by"]} if a.get("overruled_by") else {})}
            for a in diagnosis.get("alternatives") or [] if isinstance(a, dict)],
        "primary_rationale": "; ".join(
            e["interpretation"] for e in evidence[:2] if isinstance(e, dict) and e.get("interpretation")
        ) or (state.limitations[0] if state.limitations else ""),
    }
