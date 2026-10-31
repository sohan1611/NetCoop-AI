"""Shared rule-based diagnosis configuration.

Thresholds use canonical telemetry units. ``warn`` is the point where a
metric starts contributing to a hypothesis and ``crit`` is the point where it
contributes at full strength. All metrics worsen as they increase except
``rssi_dbm``: a more-negative RSSI is worse, so its critical value is lower.
Count metrics (interface errors and queue drops) are counts per collection
interval.
"""

THRESHOLDS: dict[str, dict[str, float]] = {
    "latency_ms": {"warn": 100.0, "crit": 200.0},
    "jitter_ms": {"warn": 20.0, "crit": 50.0},
    "packet_loss_pct": {"warn": 1.0, "crit": 5.0},
    "link_utilization_pct": {"warn": 75.0, "crit": 90.0},
    "rssi_dbm": {"warn": -67.0, "crit": -75.0},
    "retransmission_pct": {"warn": 2.0, "crit": 5.0},
    "cpu_pct": {"warn": 80.0, "crit": 95.0},
    "mem_pct": {"warn": 80.0, "crit": 95.0},
    "interface_errors": {"warn": 1.0, "crit": 10.0},
    "queue_drops": {"warn": 1.0, "crit": 10.0},
}

HYPOTHESES: list[str] = [
    "normal",
    "congestion",
    "weak_wifi",
    "interface_fault",
    "device_overload",
    "dns_failure",
    "dhcp_failure",
    "latency_degradation",
    "hardware_failure",
]

# T7 adds the correlator's hypothesis signatures here.
