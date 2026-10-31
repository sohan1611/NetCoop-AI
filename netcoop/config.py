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

# Service success worsens as it falls; response time worsens as it rises.
SERVICE_THRESHOLDS: dict[str, dict[str, float]] = {
    "success_rate_pct": {"warn": 95.0, "crit": 80.0},
    "response_ms": {"warn": 250.0, "crit": 1000.0},
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

# Correlator signatures are ``(canonical metric, weight)`` pairs.  The
# correlator grades every present metric from 0 at/below ``warn`` to 1 at
# ``crit`` (inverted for RSSI and service success rate), then computes
# ``match = sum(weight * grade) / sum(weight)``.  Coverage is the unweighted
# share of signature metrics present.  ``normal`` inverts each metric grade;
# latency_degradation deliberately excludes utilization and queue evidence.
SIGNATURES: dict[str, list[tuple[str, float]]] = {
    "normal": [
        ("latency_ms", 1.0),
        ("jitter_ms", 1.0),
        ("packet_loss_pct", 1.0),
        ("link_utilization_pct", 1.0),
        ("rssi_dbm", 1.0),
        ("retransmission_pct", 1.0),
        ("cpu_pct", 1.0),
        ("mem_pct", 1.0),
        ("interface_errors", 1.0),
        ("queue_drops", 1.0),
    ],
    "congestion": [
        ("link_utilization_pct", 0.45),
        ("queue_drops", 0.35),
        ("latency_ms", 0.20),
    ],
    "weak_wifi": [
        ("rssi_dbm", 0.65),
        ("retransmission_pct", 0.25),
        ("packet_loss_pct", 0.10),
    ],
    "interface_fault": [
        ("interface_errors", 0.65),
        ("packet_loss_pct", 0.35),
    ],
    "device_overload": [("cpu_pct", 0.60), ("mem_pct", 0.40)],
    "dns_failure": [
        ("dns_status", 0.40),
        ("dns_success_rate_pct", 0.40),
        ("dns_response_ms", 0.20),
    ],
    "dhcp_failure": [
        ("dhcp_status", 0.40),
        ("dhcp_success_rate_pct", 0.40),
        ("dhcp_response_ms", 0.20),
    ],
    "latency_degradation": [("latency_ms", 0.65), ("jitter_ms", 0.35)],
    "hardware_failure": [
        ("status", 0.25),
        ("cpu_pct", 0.20),
        ("interface_errors", 0.25),
        ("packet_loss_pct", 0.15),
        ("latency_ms", 0.15),
    ],
}

# Relevance weights resolve agent disagreement without allowing an agent to
# dominate hypotheses outside its domain.  They affect ranking/tie-breaking;
# the reported confidence still follows the documented metric formula.
AGENT_RELEVANCE: dict[str, dict[str, float]] = {
    "normal": {"performance": 1.0, "infrastructure": 1.0, "connectivity": 1.0},
    "congestion": {"performance": 1.0, "infrastructure": 0.9, "connectivity": 0.2},
    "weak_wifi": {"performance": 1.0, "infrastructure": 0.1, "connectivity": 0.2},
    "interface_fault": {"performance": 0.2, "infrastructure": 1.0, "connectivity": 0.9},
    "device_overload": {"performance": 0.1, "infrastructure": 1.0, "connectivity": 0.2},
    "dns_failure": {"performance": 0.1, "infrastructure": 0.2, "connectivity": 1.0},
    "dhcp_failure": {"performance": 0.1, "infrastructure": 0.2, "connectivity": 1.0},
    "latency_degradation": {"performance": 1.0, "infrastructure": 0.2, "connectivity": 0.8},
    "hardware_failure": {"performance": 0.1, "infrastructure": 1.0, "connectivity": 0.8},
}

# Confidence and adaptive-query policy.  Confidence is
# match * (0.5 + 0.5 * coverage) * dq_factor * agreement_factor.
AGREEMENT_FACTOR_MIN = 0.70
AGREEMENT_FACTOR_MAX = 1.00
AGENT_SUPPORT_RANK_WEIGHT = 0.15
QUERY_CONFIDENCE_THRESHOLD = 0.50
QUERY_MARGIN_THRESHOLD = 0.15
INSUFFICIENT_CONFIDENCE_THRESHOLD = 0.30
MIN_SIGNATURE_COVERAGE = 0.40
ALTERNATIVE_CONFIDENCE_WINDOW = 0.25
MAX_CORRELATION_ROUNDS = 2
MAX_SUPPORTING_EVIDENCE = 5

# Canonical scenario files use null RSSI/retransmission values on non-radio
# devices.  The normalizer currently records each null as a wrong-type issue.
# Those absent optional values are already reflected in signature coverage, so
# the correlator avoids applying the same penalty a second time.  Every other
# normalizer issue continues to reduce ``dq_factor`` via its reported score.
OPTIONAL_RADIO_DQ_ISSUE_FRAGMENTS = (
    ".rssi_dbm; expected a number",
    ".retransmission_pct; expected a number",
)
