"""Seeded version of the organizer's notebook telemetry generator."""

from __future__ import annotations

import random
from typing import Any


FIXED_TIMESTAMP = "2026-10-31T09:00:00Z"


def generate_telemetry(fault_type: str = "normal", *, seed: int = 0) -> dict[str, Any]:
    """Generate one deterministic organizer-format telemetry record."""
    rng = random.Random(seed)
    data: dict[str, Any] = {
        "timestamp": FIXED_TIMESTAMP,
        "device_id": f"SW_CAMPUS_{rng.randint(1, 10)}",
        "metrics": {},
    }

    if fault_type == "latency_spike":
        data["metrics"] = {
            "cpu_utilization_pct": round(rng.uniform(40, 70), 2),
            "link_utilization_pct": round(rng.uniform(60, 85), 2),
            "packet_loss_pct": round(rng.uniform(0.1, 1.5), 2),
            "latency_ms": round(rng.uniform(150, 400), 2),
            "jitter_ms": round(rng.uniform(30, 80), 2),
            "queue_drops": rng.randint(0, 50),
        }
        data["status"] = "Warning"
    elif fault_type == "hardware_failure":
        data["metrics"] = {
            "cpu_utilization_pct": 99.9,
            "link_utilization_pct": 100.0,
            "packet_loss_pct": round(rng.uniform(15, 30), 2),
            "latency_ms": round(rng.uniform(500, 1000), 2),
            "queue_drops": rng.randint(5000, 15000),
            "interface_errors": rng.randint(100, 500),
        }
        data["status"] = "Critical"
    else:
        data["metrics"] = {
            "cpu_utilization_pct": round(rng.uniform(10, 35), 2),
            "link_utilization_pct": round(rng.uniform(20, 50), 2),
            "packet_loss_pct": 0.0,
            "latency_ms": round(rng.uniform(5, 20), 2),
            "jitter_ms": round(rng.uniform(1, 5), 2),
            "queue_drops": 0,
        }
        data["status"] = "Healthy"

    return data
