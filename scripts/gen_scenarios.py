#!/usr/bin/env python3
"""Generate deterministic NetCoop diagnosis scenarios."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import random
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.generator.organizer_generator import generate_telemetry


SEED = 20261031
COLLECTED_AT = "2026-10-31T09:00:00Z"
SCENARIO_DIR = ROOT / "data" / "scenarios"

DEVICE_SPECS = (
    ("CORE_RTR_01", "router", "core", "DC"),
    ("HOSTEL_DIST_SW_01", "switch", "distribution", "Hostel"),
    ("ACADEMIC_DIST_SW_01", "switch", "distribution", "Academic Block"),
    ("HOSTEL_AP_01", "ap", "access", "Hostel AP Zone"),
    ("DEPT_SW_01", "switch", "access", "Department Ethernet"),
    ("LECTURE_AP_01", "ap", "access", "Lecture Hall"),
    ("DNS_SRV_01", "server", "service", "DC"),
    ("DHCP_SRV_01", "server", "service", "DC"),
)

LINK_SPECS = (
    ("CORE_RTR_01", "HOSTEL_DIST_SW_01", 1000),
    ("CORE_RTR_01", "ACADEMIC_DIST_SW_01", 1000),
    ("HOSTEL_DIST_SW_01", "HOSTEL_AP_01", 1000),
    ("ACADEMIC_DIST_SW_01", "DEPT_SW_01", 1000),
    ("ACADEMIC_DIST_SW_01", "LECTURE_AP_01", 1000),
    ("CORE_RTR_01", "DNS_SRV_01", 1000),
    ("CORE_RTR_01", "DHCP_SRV_01", 1000),
)

LABELS = {
    "normal": "normal",
    "congestion": "congestion",
    "weak_wifi": "weak_wifi",
    "interface_fault": "interface_fault",
    "device_overload": "device_overload",
    "dns_failure": "dns_failure",
    "dhcp_failure": "dhcp_failure",
    "interop_renamed_fields_units": "congestion",
    "schema_v0_legacy": "device_overload",
    "missing_sections": "normal",
    "malformed_json": "error",
    "conflicting_evidence": "insufficient_evidence",
    "insufficient_data": "insufficient_evidence",
    "org_normal": "normal",
    "org_latency_spike": "latency_degradation",
    "org_hardware_failure": "hardware_failure",
}


def _topology() -> dict[str, list[dict[str, Any]]]:
    return {
        "nodes": [
            {"id": device_id, "type": kind, "role": role, "location": location}
            for device_id, kind, role, location in DEVICE_SPECS
        ],
        "links": [
            {"src": src, "dst": dst, "capacity_mbps": capacity}
            for src, dst, capacity in LINK_SPECS
        ],
    }


def _canonical(name: str, rng: random.Random) -> dict[str, Any]:
    devices = []
    samples = []
    for device_id, kind, _, _ in DEVICE_SPECS:
        is_ap = kind == "ap"
        bandwidth = 300 if is_ap else 1000
        devices.append(
            {
                "device_id": device_id,
                "type": kind,
                "status": "up",
                "cpu_pct": round(rng.uniform(18, 36), 2),
                "mem_pct": round(rng.uniform(28, 48), 2),
                "interface_errors": 0,
                "queue_drops": 0,
            }
        )
        samples.append(
            {
                "device_id": device_id,
                "timestamp": COLLECTED_AT,
                "latency_ms": round(rng.uniform(4, 16), 2),
                "jitter_ms": round(rng.uniform(0.5, 3), 2),
                "packet_loss_pct": 0.0,
                "throughput_mbps": round(bandwidth * rng.uniform(0.2, 0.4), 2),
                "bandwidth_mbps": bandwidth,
                "link_utilization_pct": round(rng.uniform(20, 40), 2),
                "rssi_dbm": round(rng.uniform(-62, -48), 2) if is_ap else None,
                "retransmission_pct": round(rng.uniform(0.5, 3), 2) if is_ap else None,
            }
        )
    return {
        "schema_version": "1.0",
        "incident_id": f"INC-T3-{name.upper().replace('_', '-')}",
        "collected_at": COLLECTED_AT,
        "topology": _topology(),
        "devices": devices,
        "samples": samples,
        "services": [
            {
                "name": "dns",
                "server_id": "DNS_SRV_01",
                "status": "up",
                "success_rate_pct": 99.9,
                "response_ms": 12.0,
            },
            {
                "name": "dhcp",
                "server_id": "DHCP_SRV_01",
                "status": "up",
                "success_rate_pct": 99.8,
                "response_ms": 18.0,
            },
        ],
        "symptoms": [],
    }


def _row(rows: list[dict[str, Any]], key: str, value: str) -> dict[str, Any]:
    return next(row for row in rows if row[key] == value)


def _build_scenarios() -> dict[str, Any]:
    rng = random.Random(SEED)
    scenarios: dict[str, Any] = {}

    scenarios["normal"] = _canonical("normal", rng)

    congestion = _canonical("congestion", rng)
    congestion_device = _row(congestion["devices"], "device_id", "CORE_RTR_01")
    congestion_device.update(status="degraded", queue_drops=275)
    congestion_sample = _row(congestion["samples"], "device_id", "CORE_RTR_01")
    congestion_sample.update(
        latency_ms=185.0,
        jitter_ms=42.0,
        packet_loss_pct=4.5,
        throughput_mbps=940.0,
        bandwidth_mbps=1000,
        link_utilization_pct=94.0,
    )
    congestion["symptoms"] = ["Campus-wide slowness during peak traffic"]
    scenarios["congestion"] = congestion

    weak_wifi = _canonical("weak_wifi", rng)
    wifi_device = _row(weak_wifi["devices"], "device_id", "HOSTEL_AP_01")
    wifi_device["status"] = "degraded"
    wifi_sample = _row(weak_wifi["samples"], "device_id", "HOSTEL_AP_01")
    wifi_sample.update(
        latency_ms=95.0,
        jitter_ms=28.0,
        packet_loss_pct=7.5,
        rssi_dbm=-82.0,
        retransmission_pct=23.0,
    )
    weak_wifi["symptoms"] = ["Hostel users report weak Wi-Fi and repeated reconnects"]
    scenarios["weak_wifi"] = weak_wifi

    interface_fault = _canonical("interface_fault", rng)
    interface_device = _row(interface_fault["devices"], "device_id", "DEPT_SW_01")
    interface_device.update(status="degraded", interface_errors=1450)
    interface_sample = _row(interface_fault["samples"], "device_id", "DEPT_SW_01")
    interface_sample.update(latency_ms=140.0, jitter_ms=35.0, packet_loss_pct=18.0)
    interface_fault["symptoms"] = ["Department Ethernet sessions drop intermittently"]
    scenarios["interface_fault"] = interface_fault

    device_overload = _canonical("device_overload", rng)
    overloaded = _row(device_overload["devices"], "device_id", "ACADEMIC_DIST_SW_01")
    overloaded.update(status="degraded", cpu_pct=96.0, mem_pct=94.0)
    overload_sample = _row(device_overload["samples"], "device_id", "ACADEMIC_DIST_SW_01")
    overload_sample.update(latency_ms=88.0, jitter_ms=24.0)
    device_overload["symptoms"] = ["Academic network management plane responds slowly"]
    scenarios["device_overload"] = device_overload

    dns_failure = _canonical("dns_failure", rng)
    dns_device = _row(dns_failure["devices"], "device_id", "DNS_SRV_01")
    dns_device["status"] = "down"
    dns_service = _row(dns_failure["services"], "name", "dns")
    dns_service.update(status="down", success_rate_pct=3.0, response_ms=1800.0)
    dns_failure["symptoms"] = ["Hostnames do not resolve while IP connectivity remains available"]
    scenarios["dns_failure"] = dns_failure

    dhcp_failure = _canonical("dhcp_failure", rng)
    dhcp_device = _row(dhcp_failure["devices"], "device_id", "DHCP_SRV_01")
    dhcp_device["status"] = "down"
    dhcp_service = _row(dhcp_failure["services"], "name", "dhcp")
    dhcp_service.update(status="down", success_rate_pct=2.0, response_ms=2200.0)
    dhcp_failure["symptoms"] = ["New clients cannot obtain network addresses"]
    scenarios["dhcp_failure"] = dhcp_failure

    interop_base = _canonical("interop_renamed_fields_units", rng)
    interop_topology = deepcopy(interop_base["topology"])
    for link in interop_topology["links"]:
        link["capacity_kbps"] = link.pop("capacity_mbps") * 1000
    scenarios["interop_renamed_fields_units"] = {
        "schemaVersion": "1.0",
        "incidentId": "INC-T3-INTEROP-RENAMED-FIELDS-UNITS",
        "collectedAt": COLLECTED_AT,
        "topology": interop_topology,
        "devices": [
            {
                "device": "CORE_RTR_01",
                "type": "router",
                "status": "degraded",
                "cpu": 67.0,
                "memory": 58.0,
                "errors": 0,
                "drops": 310,
            }
        ],
        "samples": [
            {
                "device": "CORE_RTR_01",
                "timestamp": COLLECTED_AT,
                "lat": 205.0,
                "jitter_s": 0.048,
                "loss": 5.0,
                "throughput_kbps": 960000,
                "bandwidth_kbps": 1000000,
                "util": 96.0,
                "rssi": None,
                "retransmission": None,
            }
        ],
        "services": deepcopy(interop_base["services"]),
        "symptoms": ["Interoperability feed reports peak-hour congestion"],
    }

    scenarios["schema_v0_legacy"] = {
        "schema_version": "0",
        "incident_id": "INC-T3-SCHEMA-V0-LEGACY",
        "collected_at": COLLECTED_AT,
        "readings": [
            {
                "id": "ACADEMIC_DIST_SW_01",
                "timestamp": COLLECTED_AT,
                "type": "switch",
                "status": "degraded",
                "cpu": 97.0,
                "mem": 95.0,
                "errors": 2,
                "drops": 12,
                "lat": 92.0,
                "jitter": 25.0,
                "loss": 1.0,
                "throughput": 620.0,
                "bandwidth": 1000.0,
                "util": 62.0,
            }
        ],
        "symptoms": ["Legacy collector reports an overloaded distribution switch"],
    }

    missing_sections = _canonical("missing_sections", rng)
    missing_sections.pop("topology")
    missing_sections.pop("services")
    scenarios["missing_sections"] = missing_sections

    conflicting = _canonical("conflicting_evidence", rng)
    conflict_switch = _row(conflicting["devices"], "device_id", "ACADEMIC_DIST_SW_01")
    conflict_switch.update(status="degraded", cpu_pct=95.0, mem_pct=94.0)
    conflict_ap = _row(conflicting["devices"], "device_id", "LECTURE_AP_01")
    conflict_ap["status"] = "degraded"
    conflict_sample = _row(conflicting["samples"], "device_id", "LECTURE_AP_01")
    conflict_sample.update(rssi_dbm=-81.0, retransmission_pct=22.0, packet_loss_pct=8.0)
    conflicting["symptoms"] = ["Mixed reports of slow switching and weak lecture-hall Wi-Fi"]
    scenarios["conflicting_evidence"] = conflicting

    insufficient = _canonical("insufficient_data", rng)
    insufficient["devices"] = [{"device_id": "HOSTEL_AP_01", "type": "ap", "status": "up"}]
    insufficient["samples"] = [
        {"device_id": "HOSTEL_AP_01", "timestamp": COLLECTED_AT, "latency_ms": 18.0}
    ]
    insufficient["symptoms"] = ["One user reports intermittent slowness"]
    scenarios["insufficient_data"] = insufficient

    scenarios["org_normal"] = generate_telemetry("normal", seed=SEED + 1)
    scenarios["org_latency_spike"] = generate_telemetry("latency_spike", seed=SEED + 2)
    scenarios["org_hardware_failure"] = generate_telemetry("hardware_failure", seed=SEED + 3)
    return scenarios


def _write_json(path: Path, payload: Any) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    scenarios = _build_scenarios()
    if set(scenarios) | {"malformed_json"} != set(LABELS):
        raise RuntimeError("scenario and label names differ")
    for name, payload in scenarios.items():
        _write_json(SCENARIO_DIR / f"{name}.json", payload)
    (SCENARIO_DIR / "malformed_json.json").write_text(
        '{"schema_version":"1.0","incident_id":"INC-T3-MALFORMED",\n',
        encoding="utf-8",
        newline="\n",
    )
    _write_json(SCENARIO_DIR / "labels.json", LABELS)


if __name__ == "__main__":
    main()
