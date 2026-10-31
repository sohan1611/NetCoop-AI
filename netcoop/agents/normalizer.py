"""Pure telemetry normalization plus the protocol-facing normalizer agent."""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel

from netcoop import llm
from netcoop.agents.base import Agent
from netcoop.config import INSUFFICIENT_CONFIDENCE_THRESHOLD
from netcoop.protocol import DataQuality, Envelope, Finding, MsgType


log = logging.getLogger(__name__)

# Aliases apply only when the target belongs to the row being normalized.
FIELD_ALIASES: dict[str, str] = {
    "device": "device_id",
    "deviceid": "device_id",
    "node_id": "device_id",
    "lat": "latency_ms",
    "latency": "latency_ms",
    "rtt": "latency_ms",
    "rtt_ms": "latency_ms",
    "jitter": "jitter_ms",
    "cpu": "cpu_pct",
    "cpu_utilization": "cpu_pct",
    "cpu_utilization_pct": "cpu_pct",
    "mem": "mem_pct",
    "memory": "mem_pct",
    "memory_pct": "mem_pct",
    "memory_utilization_pct": "mem_pct",
    "loss": "packet_loss_pct",
    "packet_loss": "packet_loss_pct",
    "util": "link_utilization_pct",
    "utilization": "link_utilization_pct",
    "link_util": "link_utilization_pct",
    "link_utilization": "link_utilization_pct",
    "queue_drop": "queue_drops",
    "interface_error": "interface_errors",
    "throughput": "throughput_mbps",
    "bandwidth": "bandwidth_mbps",
    "capacity": "capacity_mbps",
    "rssi": "rssi_dbm",
    "retransmission": "retransmission_pct",
    "retransmissions": "retransmission_pct",
    "response_time": "response_ms",
    "response_time_ms": "response_ms",
    "success_rate": "success_rate_pct",
    "errors": "interface_errors",
    "drops": "queue_drops",
}

# Conversion is inferred only from an explicit unit-bearing key. In particular,
# canonical *_pct values are left alone; only *_frac and *_ratio signal 0..1 -> %.
UNIT_CONVERSIONS: dict[str, tuple[str, float]] = {
    "latency_s": ("latency_ms", 1000.0),
    "lat_s": ("latency_ms", 1000.0),
    "rtt_s": ("latency_ms", 1000.0),
    "jitter_s": ("jitter_ms", 1000.0),
    "throughput_kbps": ("throughput_mbps", 0.001),
    "bandwidth_kbps": ("bandwidth_mbps", 0.001),
    "capacity_kbps": ("capacity_mbps", 0.001),
    "cpu_frac": ("cpu_pct", 100.0),
    "cpu_ratio": ("cpu_pct", 100.0),
    "cpu_utilization_frac": ("cpu_pct", 100.0),
    "cpu_utilization_ratio": ("cpu_pct", 100.0),
    "mem_frac": ("mem_pct", 100.0),
    "mem_ratio": ("mem_pct", 100.0),
    "memory_frac": ("mem_pct", 100.0),
    "memory_ratio": ("mem_pct", 100.0),
    "loss_frac": ("packet_loss_pct", 100.0),
    "loss_ratio": ("packet_loss_pct", 100.0),
    "packet_loss_frac": ("packet_loss_pct", 100.0),
    "packet_loss_ratio": ("packet_loss_pct", 100.0),
    "util_frac": ("link_utilization_pct", 100.0),
    "util_ratio": ("link_utilization_pct", 100.0),
    "utilization_frac": ("link_utilization_pct", 100.0),
    "utilization_ratio": ("link_utilization_pct", 100.0),
    "link_utilization_frac": ("link_utilization_pct", 100.0),
    "link_utilization_ratio": ("link_utilization_pct", 100.0),
    "retransmission_frac": ("retransmission_pct", 100.0),
    "retransmission_ratio": ("retransmission_pct", 100.0),
    "success_rate_frac": ("success_rate_pct", 100.0),
    "success_rate_ratio": ("success_rate_pct", 100.0),
}

UNIT_SUFFIX_CONVERSIONS: dict[str, float] = {
    "_frac": 100.0,
    "_ratio": 100.0,
    "_kbps": 0.001,
    "_s": 1000.0,
}

ISSUE_PENALTIES = {
    "missing": 0.05,
    "stale": 0.05,
    "out_of_range": 0.10,
    "duplicate": 0.08,
    "wrong_type": 0.08,
    "unknown": 0.02,
    "severity": 0.05,
    "llm_mapped": 0.0,
    "coerced": 0.0,
    "expected_missing": 0.0,
    "no_timestamp": 0.01,
    "conflict": 0.10,
    "ambiguous": 0.10,
}
KIND_PENALTY_CAP = 0.25  # one kind of problem can never cost more than this
KIND_REPEAT_CAP = 5      # repeats beyond this add nothing
MAX_REPORTED_ISSUES = 25
MAX_MAPPINGS = 1000  # per run; the details endpoint shows at most 50
_UNIT_SUFFIXES = (("_pct", "%"), ("_ms", "ms"), ("_mbps", "Mbps"), ("_dbm", "dBm"))
_RECORD_KEY = re.compile(r"[A-Za-z_][A-Za-z_.]*\[\d+\]")
_INDEX = re.compile(r"\[\d+\]")

_NUMERIC_STRING = re.compile(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*[A-Za-z%/]{0,8}\s*$")

SECTION_FIELDS = {
    "nodes": {"id", "type", "role", "location"},
    "links": {"src", "dst", "capacity_mbps"},
    "devices": {
        "device_id", "type", "status", "cpu_pct", "mem_pct",
        "interface_errors", "queue_drops",
    },
    "samples": {
        "device_id", "timestamp", "latency_ms", "jitter_ms",
        "packet_loss_pct", "throughput_mbps", "bandwidth_mbps",
        "link_utilization_pct", "rssi_dbm", "retransmission_pct",
    },
    "services": {
        "name", "server_id", "status", "success_rate_pct", "response_ms",
    },
}
SECTION_REQUIRED = {
    "nodes": {"id"},
    "links": {"src", "dst"},
    "devices": {"device_id"},
    "samples": {"device_id", "timestamp"},
    "services": {"name", "server_id"},
}
NUMERIC_FIELDS = {
    "capacity_mbps", "cpu_pct", "mem_pct", "interface_errors", "queue_drops",
    "latency_ms", "jitter_ms", "packet_loss_pct", "throughput_mbps",
    "bandwidth_mbps", "link_utilization_pct", "rssi_dbm",
    "retransmission_pct", "success_rate_pct", "response_ms",
}
PERCENT_FIELDS = {name for name in NUMERIC_FIELDS if name.endswith("_pct")}
TIME_FIELDS = {"latency_ms", "jitter_ms", "response_ms"}
NON_NEGATIVE_FIELDS = NUMERIC_FIELDS - {"rssi_dbm"} - PERCENT_FIELDS
TOP_LEVEL_ALIASES = {
    "version": "schema_version",
    "schemaVersion": "schema_version", "incident": "incident_id",
    "incidentId": "incident_id", "collected": "collected_at",
    "collectedAt": "collected_at",
}
TOP_LEVEL_FIELDS = {
    "schema_version", "incident_id", "collected_at", "topology", "devices",
    "samples", "services", "symptoms",
}


# Size caps (V1). Set above the 20k-record hostile probe, which must still diagnose.
MAX_DEVICES = 25_000
MAX_SAMPLES = 50_000
MAX_NODES = 200
MAX_LINKS = 1000
MAX_SERVICES = 200


class NormalizeResult(BaseModel):
    """A no-throw normalization outcome."""

    telemetry: dict[str, Any] | None
    data_quality: DataQuality
    error: str | None = None
    mappings: list[dict[str, Any]] = []
    formats: list[str] = []
    quarantined: list[dict[str, Any]] = []


@dataclass
class _QualityTracker:
    use_llm: bool = False
    issues: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    total_records: int = 0
    source_format: str = "canonical v1"
    mappings: list[dict[str, Any]] = field(default_factory=list)
    format_flags: set[str] = field(default_factory=set)
    quarantined: list[dict[str, Any]] = field(default_factory=list)
    valid_coverage: float = 1.0

    def map(self, device_id: Any, raw_path: str, raw_value: Any, metric: str,
            value: Any, *, raw_key: str = "", factor: float = 1.0) -> None:
        """Record how one raw field became a canonical metric (for the details view)."""
        notes = []
        if raw_key and raw_key != metric:
            notes.append(f"alias {raw_key} -> {metric}")
            self.format_flags.add("aliased fields")
            if raw_key != _canonical_key(raw_key):
                self.format_flags.add("CamelCase fields")
        if factor != 1.0:
            notes.append(f"unit x{factor:g}")
            self.format_flags.add("unit-converted")
        if isinstance(raw_value, str) and isinstance(value, (int, float)):
            notes.append("coerced from string")
        if len(self.mappings) >= MAX_MAPPINGS:
            return
        unit = next((u for suffix, u in _UNIT_SUFFIXES if metric.endswith(suffix)), None)
        self.mappings.append({
            "source_format": self.source_format,
            "device_id": device_id if isinstance(device_id, str) else None,
            "raw_path": raw_path,
            "raw_value": raw_value if isinstance(raw_value, (str, int, float, bool)) else str(raw_value),
            "metric": metric, "value": value, "unit": unit, "note": "; ".join(notes),
        })

    def add(self, kind: str, message: str) -> None:
        self.issues.append(message)
        self.kinds.append(kind)

    def _penalty(self) -> float:
        """Per kind: weight x repeats x share of records affected, capped per kind."""
        by_kind: dict[str, list[str]] = {}
        for kind, message in zip(self.kinds, self.issues):
            by_kind.setdefault(kind, []).append(message)
        total = 0.0
        for kind, messages in by_kind.items():
            keys = {match.group(0) if (match := _RECORD_KEY.search(m)) else None
                    for m in messages}
            share = 1.0
            if None not in keys and self.total_records:
                share = min(1.0, len(keys) / self.total_records)
            raw = ISSUE_PENALTIES[kind] * min(len(messages), KIND_REPEAT_CAP) * share
            total += min(KIND_PENALTY_CAP, raw)
        return total

    def _summary(self) -> list[str]:
        """Collapse repeats of one issue (same text modulo row index) into one line."""
        counts: dict[str, int] = {}
        for message in self.issues:
            template = _INDEX.sub("[*]", message)
            counts[template] = counts.get(template, 0) + 1
        first: dict[str, str] = {}
        for message in self.issues:
            first.setdefault(_INDEX.sub("[*]", message), message)
        lines = []
        for template, count in counts.items():
            if count == 1:
                lines.append(first[template])
            elif self.total_records:
                lines.append(f"{template} ({min(count, self.total_records)}/{self.total_records} records)")
            else:
                lines.append(f"{template} (x{count})")
        if len(lines) > MAX_REPORTED_ISSUES:
            extra = len(lines) - MAX_REPORTED_ISSUES
            lines = [*lines[:MAX_REPORTED_ISSUES], f"...and {extra} more"]
        return lines

    def model(self, *, failed: bool = False) -> DataQuality:
        score = 0.0 if failed else max(0.0, min(1.0, 1.0 - self._penalty()))
        score *= self.valid_coverage
        # At most half the supplied metrics usable in any section is insufficient,
        # even when another section contains many healthy measurements.
        if self.valid_coverage <= 0.5:
            score = min(score, INSUFFICIENT_CONFIDENCE_THRESHOLD - 0.01)
        return DataQuality(score=round(score, 6), issues=self._summary())


def _error_result(message: str, tracker: _QualityTracker) -> NormalizeResult:
    if message not in tracker.issues:
        tracker.add("unknown", message)
    return NormalizeResult(telemetry=None, data_quality=tracker.model(failed=True), error=message)


def _parse_raw(raw: str | bytes | dict | list) -> dict[str, Any] | list[Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, (dict, list)):
        raise TypeError("telemetry input must decode to an object or list")
    return raw


def _canonical_key(key: str) -> str:
    """CamelCase / spaced / dashed names -> lower snake_case."""
    key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", key)
    return re.sub(r"[\s.\-]+", "_", key).strip("_").lower()


def _resolve_field(raw_key: str, section: str) -> tuple[str | None, float]:
    target, factor = _resolve_exact(raw_key, section)
    if target is not None:
        return target, factor
    canonical = _canonical_key(raw_key)
    if canonical != raw_key:
        target, factor = _resolve_exact(canonical, section)
        if target is not None:
            return target, factor
    for suffix in ("_pct", "_ms"):
        if canonical.endswith(suffix):
            target, factor = _resolve_exact(canonical[: -len(suffix)], section)
            if target is not None:
                return target, factor
    return None, 1.0


def _resolve_exact(raw_key: str, section: str) -> tuple[str | None, float]:
    if raw_key in UNIT_CONVERSIONS:
        target, factor = UNIT_CONVERSIONS[raw_key]
        return (target, factor) if target in SECTION_FIELDS[section] else (None, 1.0)
    for suffix, factor in UNIT_SUFFIX_CONVERSIONS.items():
        if not raw_key.endswith(suffix):
            continue
        base = raw_key[: -len(suffix)]
        target = FIELD_ALIASES.get(base)
        if suffix in {"_frac", "_ratio"}:
            target = target or f"{base}_pct"
            valid_unit = target.endswith("_pct")
        elif suffix == "_kbps":
            target = target or f"{base}_mbps"
            valid_unit = target.endswith("_mbps")
        else:
            target = target or f"{base}_ms"
            valid_unit = target in {"latency_ms", "jitter_ms"}
        if valid_unit and target in SECTION_FIELDS[section]:
            return target, factor
    if raw_key in SECTION_FIELDS[section]:
        return raw_key, 1.0
    target = FIELD_ALIASES.get(raw_key)
    if target in SECTION_FIELDS[section]:
        return target, 1.0
    return None, 1.0


def _convert_value(value: Any, target: str, factor: float,
                   tracker: _QualityTracker, path: str) -> Any:
    if target in NUMERIC_FIELDS:
        if value is None:
            return None
        if isinstance(value, str):
            match = _NUMERIC_STRING.match(value)
            if match is not None:
                tracker.add("coerced", f"coerced numeric string at {path}: {value!r}")
                value = float(match.group(1))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            tracker.add("wrong_type", f"wrong type for {path}; expected a number")
            return None
        try:
            converted = value * factor
        except OverflowError:  # int too large for a float
            converted = math.inf
        if not math.isfinite(converted):
            tracker.add("wrong_type", f"non-finite number at {path}; value dropped")
            return None
        return converted
    if value is not None and not isinstance(value, str):
        tracker.add("wrong_type", f"wrong type for {path}; expected a string")
        return None
    return value


def _normalize_status(value: Any, tracker: _QualityTracker, path: str,
                      *, organizer: bool = False) -> str | None:
    if not isinstance(value, str):
        tracker.add("wrong_type", f"wrong type for {path}; expected a string")
        return None
    status = value.lower()
    if organizer:
        mapped = {"healthy": "up", "warning": "degraded", "critical": "degraded"}.get(status)
        if status == "critical":
            tracker.add("severity", f"severity critical for {path.rsplit('.', 1)[0]}")
    else:
        mapped = status if status in {"up", "down", "degraded"} else None
    if mapped is None:
        tracker.add("out_of_range", f"unknown status at {path}: {value}")
    return mapped


def _map_leaf_fields(raw: dict, sections: tuple[str, ...], tracker: _QualityTracker,
                     path: str, ignored: set[str] | None = None) -> dict:
    if not tracker.use_llm:
        return raw
    unknown = [key for key, value in raw.items()
               if isinstance(key, str) and key not in (ignored or set())
               and not isinstance(value, (dict, list))
               and all(_resolve_field(key, section)[0] is None for section in sections)]
    if not unknown:
        return raw
    mappings = llm.map_unknown_fields(unknown) or {}
    result = dict(raw)
    occupied = {_resolve_field(key, section)[0] for key in raw if isinstance(key, str)
                for section in sections}
    for key in unknown:
        mapping = mappings.get(key, {})
        target, confidence = mapping.get('target'), mapping.get('confidence')
        if (isinstance(target, str) and any(target in SECTION_FIELDS[s] for s in sections)
                and isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
                and 0.7 <= confidence <= 1 and target not in occupied):
            result[target] = result.pop(key)
            occupied.add(target)
            tracker.add('llm_mapped',
                        f'llm_mapped {path}.{key} -> {target} (confidence={confidence:g})')
    return result


def _assign_metric(row: dict[str, Any], target: str, value: Any,
                   tracker: _QualityTracker, path: str) -> None:
    """Keep the canonical-first value only when aliases agree within 1%."""
    if target in NUMERIC_FIELDS and target in row:
        previous = row[target]
        if previous is None:
            return  # Never resurrect an already ambiguous field.
        if not math.isclose(previous, value, rel_tol=0.01):
            row[target] = None
            tracker.add("ambiguous", f"ambiguous aliases at {path}.{target}; field quarantined")
        return
    row[target] = value


def _normalize_row(raw: Any, section: str, tracker: _QualityTracker,
                   path: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        tracker.add("wrong_type", f"wrong type for {path}; expected an object")
        return None
    if section in ('devices', 'samples'):
        raw = _map_leaf_fields(raw, (section,), tracker, path)
    result: dict[str, Any] = {}
    owner = next((raw[k] for k in ("device_id", "device", "deviceId", "node_id", "server_id")
                  if isinstance(raw.get(k), str)), None)
    for key, value in sorted(raw.items(), key=lambda item: (
            item[0] not in SECTION_FIELDS[section], str(item[0]))):
        if not isinstance(key, str):
            tracker.add("unknown", f"unknown field {path}.{key!r} dropped")
            continue
        target, factor = _resolve_field(key, section)
        if target is None:
            tracker.add("unknown", f"unknown field {path}.{key} dropped")
            continue
        converted = (
            _normalize_status(value, tracker, f"{path}.{key}")
            if target == "status"
            else _convert_value(value, target, factor, tracker, f"{path}.{key}")
        )
        if converted is not None:
            _assign_metric(result, target, converted, tracker, path)
            if section in ("devices", "samples", "services") and (
                    target in NUMERIC_FIELDS or target == "status"):
                tracker.map(owner, key, value, target, converted, raw_key=key, factor=factor)
    for required in sorted(SECTION_REQUIRED[section] - result.keys()):
        tracker.add("no_timestamp" if required == "timestamp" else "missing",
                    f"missing field {path}.{required}")
    return result


def _section_rows(raw: Any, section: str, tracker: _QualityTracker,
                  path: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        tracker.add("wrong_type", f"wrong type for {path}; expected a list")
        return []
    limit = {"nodes": MAX_NODES, "links": MAX_LINKS, "services": MAX_SERVICES}.get(section)
    if limit is not None and len(raw) > limit:
        tracker.add("expected_missing", f"{path} truncated to {limit} records (received {len(raw)})")
        raw = raw[:limit]
    rows = []
    for index, row in enumerate(raw):
        normalized = _normalize_row(row, section, tracker, f"{path}[{index}]")
        if normalized is not None:
            rows.append(normalized)
    return rows


def _split_metric_row(row: dict[str, Any], tracker: _QualityTracker, path: str,
                      *, organizer: bool,
                      default_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    device: dict[str, Any] = {}
    sample: dict[str, Any] = {}
    device_id = row.get("device_id", row.get("device", row.get("id")))
    if isinstance(device_id, str):
        device["device_id"] = sample["device_id"] = device_id
    elif device_id is None and default_id is not None:
        device["device_id"] = sample["device_id"] = default_id
        tracker.add("missing", f"missing field {path}.device_id; using {default_id}")
    elif device_id is None:
        tracker.add("missing", f"missing field {path}.device_id")
    else:
        tracker.add("wrong_type", f"wrong type for {path}.device_id; expected a string")

    timestamp = row.get("timestamp")
    if isinstance(timestamp, str):
        sample["timestamp"] = timestamp
    elif timestamp is None:
        tracker.add("no_timestamp", f"missing field {path}.timestamp")
    else:
        tracker.add("wrong_type", f"wrong type for {path}.timestamp; expected a string")

    if "status" in row:
        status = _normalize_status(row["status"], tracker, f"{path}.status", organizer=organizer)
        if status is not None:
            device["status"] = status
            tracker.map(device.get("device_id"), "status", row["status"], "status", status)
    if "type" in row:
        device_type = _convert_value(row["type"], "type", 1.0, tracker, f"{path}.type")
        if device_type is not None:
            device["type"] = device_type

    metrics = row.get("metrics") if organizer else row
    if not isinstance(metrics, dict):
        tracker.add("wrong_type", f"wrong type for {path}.metrics; expected an object")
        return device, sample
    ignored = {"device_id", "device", "id", "timestamp", "status", "type", "metrics"}
    metrics = _map_leaf_fields(metrics, ('devices', 'samples'), tracker,
                               f'{path}.metrics', ignored)
    for key, value in sorted(metrics.items(), key=lambda item: (
            item[0] not in NUMERIC_FIELDS, str(item[0]))):
        if key in ignored:
            continue
        if not isinstance(key, str):
            tracker.add("unknown", f"unknown field {path}.metrics.{key!r} dropped")
            continue
        device_target, device_factor = _resolve_field(key, "devices")
        sample_target, sample_factor = _resolve_field(key, "samples")
        raw_path = f"metrics.{key}" if organizer else key
        if device_target is not None:
            converted = _convert_value(value, device_target, device_factor, tracker,
                                       f"{path}.metrics.{key}")
            if converted is not None:
                _assign_metric(device, device_target, converted, tracker, path)
                tracker.map(device.get("device_id"), raw_path, value, device_target,
                            converted, raw_key=key, factor=device_factor)
        elif sample_target is not None:
            converted = _convert_value(value, sample_target, sample_factor, tracker,
                                       f"{path}.metrics.{key}")
            if converted is not None:
                _assign_metric(sample, sample_target, converted, tracker, path)
                tracker.map(device.get("device_id"), raw_path, value, sample_target,
                            converted, raw_key=key, factor=sample_factor)
        else:
            tracker.add("unknown", f"unknown field {path}.metrics.{key} dropped")

    if organizer:
        for key in row.keys() - ignored:
            tracker.add("unknown", f"unknown field {path}.{key} dropped")
    return device, sample


def _merge_devices(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for row in rows:
        device_id = row.get("device_id")
        if isinstance(device_id, str):
            merged.setdefault(device_id, {}).update(row)
        else:
            anonymous.append(row)
    return [*merged.values(), *anonymous]


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


def _latest_timestamp(samples: list[dict[str, Any]]) -> str | None:
    valid = [
        (parsed, sample.get("timestamp"))
        for sample in samples
        if (parsed := _parse_timestamp(sample.get("timestamp"))) is not None
    ]
    return max(valid, key=lambda item: item[0])[1] if valid else None


def _base_telemetry(*, incident_id: Any = "UNKNOWN", collected_at: Any = None,
                    devices: list[dict[str, Any]] | None = None,
                    samples: list[dict[str, Any]] | None = None,
                    symptoms: list[str] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "incident_id": incident_id,
        "collected_at": collected_at,
        "topology": {"nodes": [], "links": []},
        "devices": devices or [],
        "samples": samples or [],
        "services": [],
        "symptoms": symptoms or [],
    }


def _normalize_symptoms(raw: Any, tracker: _QualityTracker) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        tracker.add("wrong_type", "wrong type for root.symptoms; expected a list")
        return []
    symptoms = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            symptoms.append(item)
        else:
            tracker.add("wrong_type", f"wrong type for root.symptoms[{index}]; expected a string")
    return symptoms


def _normalize_organizer(rows: list[Any], tracker: _QualityTracker) -> dict[str, Any]:
    tracker.source_format = "organizer"
    devices: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        path = f"organizer[{index}]"
        if not isinstance(row, dict):
            tracker.add("wrong_type", f"wrong type for {path}; expected an object")
            continue
        device, sample = _split_metric_row(row, tracker, path, organizer=True,
                                       default_id=f"UNKNOWN_DEVICE_{index}")
        devices.append(device)
        samples.append(sample)
    tracker.total_records = len(rows)
    tracker.add("expected_missing", "missing section topology; using empty topology")
    tracker.add("expected_missing", "missing section services; using empty list")
    return _base_telemetry(
        collected_at=_latest_timestamp(samples),
        devices=_merge_devices(devices),
        samples=samples,
    )


def _normalize_legacy(source: dict[str, Any], tracker: _QualityTracker) -> dict[str, Any]:
    """Upgrade v0 flat ``nodes``/``readings`` rows to schema 1.0.

    Identity/status/CPU/memory/error fields become device rows. Timestamp and
    traffic measurements become sample rows; repeated device rows are merged.
    """
    tracker.source_format = "legacy v0"
    rows = source.get("nodes", source.get("readings", []))
    if not isinstance(rows, list):
        tracker.add("wrong_type", "wrong type for legacy rows; expected a list")
        rows = []
    devices: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        path = f"legacy[{index}]"
        if not isinstance(row, dict):
            tracker.add("wrong_type", f"wrong type for {path}; expected an object")
            continue
        device, sample = _split_metric_row(row, tracker, path, organizer=False)
        devices.append(device)
        samples.append(sample)

    allowed = {
        "schema_version", "nodes", "readings", "incident_id", "collected_at", "symptoms",
    }
    tracker.total_records = len(rows)
    for key in source.keys() - allowed:
        tracker.add("unknown", f"unknown field root.{key} dropped")
    tracker.add("missing", "missing section topology; using empty topology")
    tracker.add("missing", "missing section services; using empty list")
    return _base_telemetry(
        incident_id=source.get("incident_id", "UNKNOWN"),
        collected_at=source.get("collected_at") or _latest_timestamp(samples),
        devices=_merge_devices(devices),
        samples=samples,
        symptoms=_normalize_symptoms(source.get("symptoms", []), tracker),
    )


def _optional_section(source: dict[str, Any], section: str,
                      tracker: _QualityTracker) -> list[dict[str, Any]]:
    if section not in source:
        tracker.add("missing", f"missing section {section}; using empty list")
        return []
    return _section_rows(source[section], section, tracker, f"root.{section}")


def _normalize_canonical(source: dict[str, Any], tracker: _QualityTracker) -> dict[str, Any]:
    normalized_source: dict[str, Any] = {}
    for key, value in source.items():
        target = TOP_LEVEL_ALIASES.get(key, key)
        if target not in TOP_LEVEL_FIELDS:
            tracker.add("unknown", f"unknown field root.{key} dropped")
        else:
            normalized_source[target] = value

    if "schema_version" not in normalized_source:
        tracker.add("missing", "missing field root.schema_version; assuming 1.0")
    if "incident_id" not in normalized_source:
        tracker.add("missing", "missing field root.incident_id")
    if "collected_at" not in normalized_source:
        tracker.add("missing", "missing field root.collected_at")

    topology_raw = normalized_source.get("topology")
    if topology_raw is None:
        tracker.add("missing", "missing section topology; using empty topology")
        topology = {"nodes": [], "links": []}
    elif not isinstance(topology_raw, dict):
        tracker.add("wrong_type", "wrong type for root.topology; expected an object")
        topology = {"nodes": [], "links": []}
    else:
        for key in topology_raw.keys() - {"nodes", "links"}:
            tracker.add("unknown", f"unknown field root.topology.{key} dropped")
        if "nodes" not in topology_raw:
            tracker.add("missing", "missing section topology.nodes; using empty list")
        if "links" not in topology_raw:
            tracker.add("missing", "missing section topology.links; using empty list")
        topology = {
            "nodes": _section_rows(topology_raw.get("nodes", []), "nodes", tracker,
                                   "root.topology.nodes"),
            "links": _section_rows(topology_raw.get("links", []), "links", tracker,
                                   "root.topology.links"),
        }

    declared = {node["id"] for node in topology["nodes"] if node.get("id")}
    links = []
    for link in topology["links"]:
        if link.get("src") not in declared or link.get("dst") not in declared:
            tracker.add("missing", f"Dropped link {link.get('src')} -> {link.get('dst')}: endpoint is not a declared topology node")
        else:
            links.append(link)
    topology["links"] = links

    devices = _optional_section(normalized_source, "devices", tracker)
    samples = _optional_section(normalized_source, "samples", tracker)
    services = _optional_section(normalized_source, "services", tracker)
    symptoms = _normalize_symptoms(normalized_source.get("symptoms", []), tracker)
    tracker.total_records = (len(devices) + len(samples) + len(services)
                             + len(topology["nodes"]) + len(topology["links"]))

    incident_id = normalized_source.get("incident_id", "UNKNOWN")
    if not isinstance(incident_id, str):
        tracker.add("wrong_type", "wrong type for root.incident_id; expected a string")
        incident_id = "UNKNOWN"
    collected_at = normalized_source.get("collected_at")
    if collected_at is not None and not isinstance(collected_at, str):
        tracker.add("wrong_type", "wrong type for root.collected_at; expected a string")
        collected_at = None
    return {
        "schema_version": "1.0",
        "incident_id": incident_id,
        "collected_at": collected_at or _latest_timestamp(samples),
        "topology": topology,
        "devices": devices,
        "samples": samples,
        "services": services,
        "symptoms": symptoms,
    }


def _validate_rows(telemetry: dict[str, Any], tracker: _QualityTracker) -> None:
    def quarantine(row: dict, name: str, value: Any, reason: str) -> None:
        tracker.quarantined.append({
            "device_id": row.get("device_id", row.get("server_id")),
            "field": name, "value": value, "reason": reason,
        })

    for section in ("devices", "samples", "services"):
        for index, row in enumerate(telemetry[section]):
            for name in sorted(NUMERIC_FIELDS & row.keys()):
                value = row[name]
                if value is None:
                    continue
                limit = 60000 if name in TIME_FIELDS else 1e9
                invalid = (
                    (name in PERCENT_FIELDS and not 0 <= value <= 100)
                    or (name in NON_NEGATIVE_FIELDS and not 0 <= value <= limit)
                    or (name == "rssi_dbm" and value > 0)
                )
                if invalid:
                    reason = f"out-of-range value at {section}[{index}].{name}"
                    quarantine(row, name, value, reason)
                    tracker.add("out_of_range",
                                f"{reason}: {value}; device_id="
                                f"{row.get('device_id', row.get('server_id'))}; quarantined")
                    row[name] = None

    # Merge first so conflicts cannot be hidden by row ordering or later rows.
    seen: dict[tuple[str, datetime], dict[str, Any]] = {}
    merged: list[dict[str, Any]] = []
    for index, sample in enumerate(telemetry["samples"]):
        timestamp = _parse_timestamp(sample.get("timestamp"))
        if sample.get("timestamp") is not None and timestamp is None:
            tracker.add("wrong_type", f"invalid timestamp at samples[{index}].timestamp")
        device_id = sample.get("device_id")
        if isinstance(device_id, str) and timestamp is not None:
            sample["timestamp"] = timestamp.isoformat().replace("+00:00", "Z")
            key = (device_id, timestamp)
            if key in seen:
                tracker.add("duplicate", f"duplicate sample for device_id={device_id} "
                            f"timestamp={sample['timestamp']} merged")
                previous = seen[key]
                for name, value in sample.items():
                    if name not in previous:
                        previous[name] = value
                    elif name not in {"device_id", "timestamp"} and previous[name] != value:
                        if previous[name] is not None:
                            quarantine(sample, name, [previous[name], value], "conflict")
                            tracker.add("conflict", f"conflict for device_id={device_id} "
                                        f"timestamp={sample['timestamp']} field={name}; quarantined")
                        previous[name] = None
                continue
            seen[key] = sample
        merged.append(sample)

    reference = (_parse_timestamp(telemetry.get("collected_at"))
                 or _parse_timestamp(_latest_timestamp(merged)))
    samples = []
    for index, sample in enumerate(merged):
        timestamp = _parse_timestamp(sample.get("timestamp"))
        reason = None
        if timestamp is not None and reference is not None:
            if timestamp < reference - timedelta(hours=1):
                reason = "stale timestamp"
            elif timestamp > reference + timedelta(minutes=5):
                reason = "future timestamp"
        if reason is not None:
            quarantine(sample, "timestamp", sample["timestamp"], reason)
            tracker.add("stale", f"{reason} at samples[{index}].timestamp: "
                        f"{sample['timestamp']}; device_id={sample.get('device_id')}; "
                        "quarantined sample excluded")
        else:
            samples.append(sample)
    telemetry["samples"] = samples
    telemetry["quarantined"] = tracker.quarantined

    # Duplicated metrics count once; rejected time windows count as lost coverage.
    for section, rows in (("devices", telemetry["devices"]),
                          ("samples", merged), ("services", telemetry["services"])):
        total = sum(len(NUMERIC_FIELDS & row.keys()) for row in rows)
        valid = sum(row[name] is not None
                    for row in telemetry[section] for name in NUMERIC_FIELDS & row.keys())
        if total:
            tracker.valid_coverage = min(tracker.valid_coverage, valid / total)
    if tracker.valid_coverage < 1:
        tracker.add("coerced", f"valid-metric coverage: {tracker.valid_coverage:.1%}; "
                    "quarantined or ambiguous evidence excluded")


def normalize(raw: str | bytes | dict | list, use_llm: bool = False) -> NormalizeResult:
    """Normalize supported telemetry into schema 1.0 without raising."""
    tracker = _QualityTracker(use_llm=use_llm)
    try:
        source = _parse_raw(raw)
        if isinstance(source, dict) and not source.keys() & (TOP_LEVEL_FIELDS | {"metrics", "nodes", "readings"}):
            for wrapper in ("records", "data", "telemetry", "items"):
                if isinstance(source.get(wrapper), list):
                    tracker.add("coerced", f"unwrapped records from root.{wrapper}")
                    source = source[wrapper]
                    break
        if isinstance(source, list):
            telemetry = _normalize_organizer(source, tracker)
        elif "metrics" in source:
            telemetry = _normalize_organizer([source], tracker)
        else:
            version = source.get("schema_version", source.get("schemaVersion", source.get("version")))
            looks_legacy = version is None and ("readings" in source or "nodes" in source)
            if version in (0, 0.0, "0", "0.0", "v0") or looks_legacy:
                telemetry = _normalize_legacy(source, tracker)
            elif version in (None, 1, 1.0, "1", "1.0"):
                telemetry = _normalize_canonical(source, tracker)
            else:
                return _error_result(f"unknown schema_version: {version}", tracker)
        for section, limit in (("devices", MAX_DEVICES), ("samples", MAX_SAMPLES)):
            if len(telemetry[section]) > limit:
                return _error_result(
                    f"input too large: {len(telemetry[section])} {section} (max {limit})", tracker)
        _validate_rows(telemetry, tracker)
        return NormalizeResult(
            telemetry=telemetry, data_quality=tracker.model(),
            mappings=tracker.mappings,
            formats=[tracker.source_format, *sorted(tracker.format_flags)],
            quarantined=tracker.quarantined,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.info("invalid JSON telemetry: %s", getattr(exc, "msg", exc))
        return _error_result("invalid JSON telemetry", tracker)
    except Exception as exc:  # Defensive boundary: normalization must never crash a run.
        log.warning("normalization failed: %s: %s", type(exc).__name__, exc)
        return _error_result(f"unable to normalize telemetry: {_public_reason(exc)}", tracker)


def _public_reason(exc: BaseException) -> str:
    """Generic, non-leaky wording for an unexpected normalization failure (detail goes to logs)."""
    if isinstance(exc, RecursionError):
        return "JSON nesting too deep"
    if isinstance(exc, ValueError) and "digits" in str(exc):
        return "number too large"
    if isinstance(exc, MemoryError):
        return "input too large"
    return "invalid input"


class NormalizerAgent(Agent):
    """Turn raw TASK payloads into canonical telemetry FINDING envelopes."""

    name = "normalizer"

    def __init__(self, use_llm: bool = False) -> None:
        self.use_llm = use_llm

    @staticmethod
    def normalize(raw: str | bytes | dict | list, use_llm: bool = False) -> NormalizeResult:
        return normalize(raw, use_llm=use_llm)

    async def handle(self, envelope: Envelope) -> list[Envelope]:
        try:
            if envelope.type != MsgType.TASK:
                return [self._error_envelope(envelope, "normalizer accepts TASK envelopes only")]
            if "raw" in envelope.payload:
                raw = envelope.payload["raw"]
            elif "telemetry" in envelope.payload:
                raw = envelope.payload["telemetry"]
            else:
                return [self._error_envelope(
                    envelope, "TASK payload must contain 'raw' or 'telemetry'"
                )]
            result = normalize(raw, use_llm=self.use_llm)
            if result.error is not None or result.telemetry is None:
                return [self._error_envelope(envelope, result.error or "normalization failed")]

            affected_devices = [
                row["device_id"] for row in result.telemetry["devices"]
                if isinstance(row.get("device_id"), str)
            ]
            finding = Finding(
                agent=self.name,
                hypotheses=[],
                affected_devices=list(dict.fromkeys(affected_devices)),
                evidence=[],
                data_quality=result.data_quality,
                notes=[],
            )
            payload = finding.model_dump(mode="json")
            payload["telemetry"] = result.telemetry
            payload["mappings"] = result.mappings
            payload["formats"] = result.formats
            payload["quarantined"] = result.quarantined
            return [Envelope(
                type=MsgType.FINDING,
                sender=self.name,
                recipient=envelope.sender,
                correlation_id=envelope.correlation_id or envelope.msg_id,
                payload=payload,
            )]
        except Exception as exc:  # Protocol boundary: handle must never raise.
            log.warning("normalizer handle failed: %s: %s", type(exc).__name__, exc)
            return [self._error_envelope(envelope, f"normalization failed: {_public_reason(exc)}")]

    def _error_envelope(self, envelope: Envelope, reason: str) -> Envelope:
        return Envelope(
            type=MsgType.ERROR,
            sender=self.name,
            recipient=envelope.sender,
            correlation_id=envelope.correlation_id or envelope.msg_id,
            payload={
                "code": "NORMALIZATION_ERROR",
                "message": reason,
                "recoverable": True,
            },
        )
