"""Cause-specific actions and pure simulated telemetry repairs."""
from copy import deepcopy
from typing import Any
from netcoop.config import THRESHOLDS

_ACTIONS = {
    'normal': ['Continue monitoring network health.'],
    'congestion': ['Inspect egress queues and traffic sources.', 'Rebalance traffic or increase uplink capacity.'],
    'weak_wifi': ['Check AP placement and interference.', 'Adjust radio channels and coverage.'],
    'interface_fault': ['Inspect interface counters and cabling.', 'Replace faulty cables or transceivers.'],
    'device_overload': ['Inspect CPU and memory consumers.', 'Reduce load or expand capacity.'],
    'dns_failure': ['Check resolver configuration and DNS service.', 'Restore DNS and test name resolution.'],
    'dhcp_failure': ['Check DHCP scopes and relay configuration.', 'Restore DHCP and test lease allocation.'],
    'latency_degradation': ['Inspect path latency and jitter.', 'Repair or reroute the degraded path.'],
    'hardware_failure': ['Check power and hardware alarms.', 'Repair or replace failed hardware.'],
    'insufficient_evidence': ['Widen the telemetry collection window.', 'Verify telemetry sources and collect missing metrics.'],
}
_METRICS = {
    'congestion': {'link_utilization_pct': 35, 'queue_drops': 0},
    'weak_wifi': {'rssi_dbm': -55, 'retransmission_pct': 1},
    'interface_fault': {'interface_errors': 0},
    'device_overload': {'cpu_pct': 30, 'mem_pct': 40},
    'latency_degradation': {'latency_ms': 10, 'jitter_ms': 1},
    'hardware_failure': {'cpu_pct': 30, 'interface_errors': 0},
}
PLAYBOOK = {cause: {'actions': actions, 'remediation': (
    'Restore service/server availability, success to 100% and response to 10 ms.'
    if cause in ('dns_failure', 'dhcp_failure') else
    f'Restore {", ".join(_METRICS[cause])} and associated delay/loss on affected devices.'
    if cause in _METRICS else 'No telemetry changes.')}
    for cause, actions in _ACTIONS.items()}


def simulate_remediation(cause: str, telemetry: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy telemetry and clear cause-specific symptoms on affected rows."""
    if cause not in PLAYBOOK:
        raise ValueError(f'Unknown remediation cause: {cause}')
    patched = deepcopy(telemetry)
    rows = [*patched.get('devices', []), *patched.get('samples', [])]
    metrics = _METRICS.get(cause, {})
    affected = set()
    for row in rows:
        for metric in metrics:
            value = row.get(metric)
            if isinstance(value, (float, int)) and not isinstance(value, bool):
                warn = THRESHOLDS[metric]['warn']
                if (value < warn if metric == 'rssi_dbm' else value > warn):
                    affected.add(row.get('device_id'))
        if cause == 'hardware_failure' and row.get('status') in ('down', 'degraded'):
            affected.add(row.get('device_id'))
    if cause in ('dns_failure', 'dhcp_failure'):
        for service in patched.get('services', []):
            if service.get('name') == cause.removesuffix('_failure'):
                affected.add(service.get('server_id'))
                service.update(status='up', success_rate_pct=100, response_ms=10)
    for row in rows:
        if row.get('device_id') not in affected:
            continue
        for metric, value in metrics.items():
            if metric in row and row[metric] is not None:
                row[metric] = value
        if 'status' in row:
            row['status'] = 'up'
        for metric, value in {'latency_ms': 10, 'jitter_ms': 1, 'packet_loss_pct': 0}.items():
            if metric in row:
                row[metric] = value
    return patched
