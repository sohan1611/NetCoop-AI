"""Topology and network-service connectivity analysis."""

from __future__ import annotations

from typing import Any

import networkx as nx

from netcoop.agents.base import Agent
from netcoop.config import SERVICE_THRESHOLDS, THRESHOLDS
from netcoop.protocol import DataQuality, Envelope, Evidence, Finding, Hypothesis, MsgType


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 6)


def _quality(value: Any) -> DataQuality | None:
    if value is None:
        return None
    try:
        return DataQuality.model_validate(value)
    except Exception:
        return None


def _status_severity(status: Any) -> float:
    if not isinstance(status, str):
        return 0.0
    return {"up": 0.0, "degraded": 0.6, "down": 1.0}.get(status.lower(), 0.0)


def _service_metric_severity(metric: str, value: float) -> float:
    limits = SERVICE_THRESHOLDS[metric]
    warn, crit = limits["warn"], limits["crit"]
    if metric == "success_rate_pct":
        if value >= warn:
            return 0.0
        if value <= crit:
            return 1.0
        return _clamp(0.5 + 0.5 * (warn - value) / (warn - crit))
    if value < warn:
        return 0.0
    if value >= crit:
        return 1.0
    return _clamp(0.5 + 0.5 * (value - warn) / (crit - warn))


def _build_graph(topology: Any) -> tuple[nx.DiGraph, str | None, list[str]]:
    graph = nx.DiGraph()
    core_candidates: list[str] = []
    notes: list[str] = []
    if not isinstance(topology, dict):
        return graph, None, notes

    nodes = topology.get("nodes")
    if isinstance(nodes, list):
        for row in nodes:
            if not isinstance(row, dict):
                continue
            node_id = row.get("id")
            if not isinstance(node_id, str) or not node_id:
                continue
            graph.add_node(node_id, **row)
            if row.get("role") == "core" and row.get("type") == "router":
                core_candidates.append(node_id)

    links = topology.get("links")
    if isinstance(links, list):
        for row in links:
            if not isinstance(row, dict):
                continue
            source, target = row.get("src"), row.get("dst")
            if not isinstance(source, str) or not source:
                continue
            if not isinstance(target, str) or not target:
                continue
            graph.add_edge(source, target, **row)

    if core_candidates:
        root = core_candidates[0]
    else:
        roots = sorted(node for node, degree in graph.in_degree() if degree == 0)
        root = roots[0] if roots else None
        if graph and root is not None:
            notes.append(f"No core router was identified; topology source {root} was used as root.")
        elif graph:
            notes.append("No core router or acyclic topology source was identified.")
    return graph, root, notes


def _common_upstream(
    graph: nx.DiGraph,
    root: str | None,
    affected: list[str],
) -> str | None:
    if root is None or root not in graph:
        return None
    paths: list[list[str]] = []
    for device_id in affected:
        if device_id not in graph:
            continue
        try:
            path = nx.shortest_path(graph, root, device_id)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
        paths.append(path)
    if not paths:
        return None

    common: list[str] = []
    for entries in zip(*paths):
        if len(set(entries)) != 1:
            break
        common.append(entries[0])
    if not common:
        return None
    if len(paths) == 1 and len(common) > 1:
        return common[-2]
    return common[-1]


def _blast_radius(graph: nx.DiGraph, affected: list[str]) -> list[str]:
    impacted: set[str] = set()
    for device_id in affected:
        if device_id not in graph:
            continue
        impacted.add(device_id)
        impacted.update(nx.descendants(graph, device_id))
    return sorted(impacted)


def _isolated_segments(
    graph: nx.DiGraph,
    root: str | None,
    down_devices: set[str],
) -> list[list[str]]:
    if not graph or root is None or root not in graph:
        return []
    available = graph.copy()
    available.remove_nodes_from(node for node in down_devices if node in available)
    reachable: set[str] = set()
    if root in available:
        reachable = {root, *nx.descendants(available, root)}
    isolated = set(graph) - reachable
    if not isolated:
        return []
    return sorted(
        (
            sorted(component)
            for component in nx.connected_components(
                graph.subgraph(isolated).to_undirected()
            )
        ),
        key=lambda component: component[0],
    )


def _analyze_services(
    telemetry: dict[str, Any],
    evidence: list[Evidence],
    notes: list[str],
    affected: list[str],
) -> dict[str, float]:
    scores = {"dns_failure": 0.0, "dhcp_failure": 0.0}
    devices = telemetry.get("devices")
    device_status = (
        {
            row.get("device_id"): row.get("status")
            for row in devices
            if isinstance(row, dict) and isinstance(row.get("device_id"), str)
        }
        if isinstance(devices, list)
        else {}
    )
    services = telemetry.get("services")
    if not isinstance(services, list):
        notes.append("Missing or invalid services section; no DNS/DHCP checks were run.")
        return scores

    for row in services:
        if not isinstance(row, dict):
            continue
        service = row.get("name")
        if not isinstance(service, str) or service.lower() not in {"dns", "dhcp"}:
            continue
        service = service.lower()
        cause = f"{service}_failure"
        server_id = (
            row.get("server_id")
            if isinstance(row.get("server_id"), str)
            else None
        )
        signal = _status_severity(row.get("status"))
        status = row.get("status")
        if signal >= 0.5:
            evidence.append(Evidence(
                metric=f"{service}_status",
                value=status,
                device_id=server_id,
                interpretation=f"{service.upper()} service status is {status}",
            ))

        for metric, unit in (("success_rate_pct", "%"), ("response_ms", "ms")):
            value = _number(row.get(metric))
            if value is None:
                continue
            severity = _service_metric_severity(metric, value)
            signal = max(signal, severity)
            if severity >= 0.5:
                threshold = SERVICE_THRESHOLDS[metric]["warn"]
                direction = "below" if metric == "success_rate_pct" else "at or above"
                evidence.append(Evidence(
                    metric=f"{service}_{metric}",
                    value=round(value, 6),
                    unit=unit,
                    threshold=threshold,
                    device_id=server_id,
                    interpretation=(
                        f"{service.upper()} {metric} is {direction} the warning "
                        f"threshold {threshold:g}"
                    ),
                ))

        server_status = device_status.get(server_id)
        if server_status == "down":
            signal = max(signal, 1.0)
            notes.append(f"{service.upper()} service server {server_id} is down.")
            evidence.append(Evidence(
                metric=f"{service}_server_status",
                value="down",
                device_id=server_id,
                interpretation=f"the {service.upper()} service server itself is down",
            ))
        elif server_status == "degraded":
            signal = max(signal, 0.6)
            notes.append(f"{service.upper()} service server {server_id} is degraded.")

        if signal >= 0.5 and server_id is not None and server_id not in affected:
            affected.append(server_id)
        scores[cause] = max(scores[cause], _clamp(signal))
    return scores


def analyze_connectivity(
    telemetry: dict[str, Any],
    *,
    data_quality: DataQuality | dict[str, Any] | None = None,
) -> Finding:
    """Analyze canonical topology, reachability signals, and core services."""

    notes: list[str] = []
    evidence: list[Evidence] = []
    affected: list[str] = []
    down_devices: set[str] = set()
    topology_signals = {
        "interface_fault": 0.0,
        "hardware_failure": 0.0,
        "latency_degradation": 0.0,
    }

    devices = telemetry.get("devices") if isinstance(telemetry, dict) else None
    if isinstance(devices, list):
        for row in devices:
            if not isinstance(row, dict):
                continue
            device_id = row.get("device_id")
            if not isinstance(device_id, str) or not device_id:
                continue
            status = row.get("status")
            severity = _status_severity(status)
            if status == "down":
                down_devices.add(device_id)
            if severity < 0.5:
                continue
            if device_id not in affected:
                affected.append(device_id)
            evidence.append(Evidence(
                metric="status",
                value=status,
                device_id=device_id,
                interpretation=f"device status is {status}",
            ))
            topology_signals["hardware_failure"] = max(
                topology_signals["hardware_failure"], severity
            )

    samples = telemetry.get("samples") if isinstance(telemetry, dict) else None
    if isinstance(samples, list):
        for row in samples:
            if not isinstance(row, dict):
                continue
            device_id = row.get("device_id")
            if not isinstance(device_id, str) or not device_id:
                continue
            timestamp = (
                row.get("timestamp")
                if isinstance(row.get("timestamp"), str)
                else None
            )
            for metric, cause, unit in (
                ("packet_loss_pct", "interface_fault", "%"),
                ("latency_ms", "latency_degradation", "ms"),
            ):
                value = _number(row.get(metric))
                threshold = THRESHOLDS[metric]["warn"]
                if value is None or value < threshold:
                    continue
                if device_id not in affected:
                    affected.append(device_id)
                crit = THRESHOLDS[metric]["crit"]
                severity = (
                    1.0
                    if value >= crit
                    else 0.5 + 0.5 * (value - threshold) / (crit - threshold)
                )
                topology_signals[cause] = max(
                    topology_signals[cause], _clamp(severity)
                )
                evidence.append(Evidence(
                    metric=metric,
                    value=round(value, 6),
                    unit=unit,
                    threshold=threshold,
                    device_id=device_id,
                    timestamp=timestamp,
                    interpretation=(
                        f"{metric} is at or above the warning threshold {threshold:g}"
                    ),
                ))

    topology = telemetry.get("topology") if isinstance(telemetry, dict) else None
    graph, root, graph_notes = _build_graph(topology)
    notes.extend(graph_notes)
    if not graph:
        notes.append("no topology supplied")
    else:
        graph_affected = [device_id for device_id in affected if device_id in graph]
        upstream = _common_upstream(graph, root, graph_affected)
        if upstream is not None:
            evidence.append(Evidence(
                metric="common_upstream",
                value=upstream,
                device_id=upstream,
                interpretation=(
                    "common upstream device for affected nodes: "
                    f"{', '.join(graph_affected)}"
                ),
            ))
        blast_radius = _blast_radius(graph, graph_affected)
        if blast_radius:
            evidence.append(Evidence(
                metric="blast_radius",
                value=", ".join(blast_radius),
                device_id=upstream,
                interpretation=(
                    f"{len(blast_radius)} node(s) are affected or downstream of "
                    "affected nodes"
                ),
            ))
        for segment in _isolated_segments(graph, root, down_devices):
            evidence.append(Evidence(
                metric="isolated_segment",
                value=", ".join(segment),
                device_id=segment[0],
                interpretation=(
                    "segment is unreachable from the core router with down devices removed"
                ),
            ))

    services = telemetry.get("services") if isinstance(telemetry, dict) else None
    if not graph and not (isinstance(services, list) and services):
        # Nothing to reason about topologically or by service: abstain rather than
        # vote from per-device metrics the other agents already cover.
        notes.append("no topology/services supplied; connectivity abstains")
        return Finding(
            agent="connectivity",
            hypotheses=[],
            affected_devices=[],
            evidence=[],
            data_quality=_quality(data_quality),
            notes=notes,
        )

    service_scores = _analyze_services(telemetry, evidence, notes, affected)
    cause_scores = {**topology_signals, **service_scores}
    strongest = max(cause_scores.values(), default=0.0)
    cause_scores["normal"] = (
        0.95 if strongest == 0.0 else _clamp(1.0 - strongest)
    )
    hypotheses = [
        Hypothesis(cause=cause, score=_clamp(score))
        for cause, score in sorted(
            cause_scores.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    return Finding(
        agent="connectivity",
        hypotheses=hypotheses,
        affected_devices=affected,
        evidence=evidence,
        data_quality=_quality(data_quality),
        notes=notes,
    )


class ConnectivityAgent(Agent):
    """Diagnose topology reachability and DNS/DHCP service health."""

    name = "connectivity"

    def __init__(self) -> None:
        self._last_telemetry: dict[str, Any] = {}

    async def handle(self, envelope: Envelope) -> list[Envelope]:
        try:
            if envelope.type == MsgType.TASK:
                raw = envelope.payload.get("telemetry")
                telemetry = raw if isinstance(raw, dict) else {}
                finding = analyze_connectivity(
                    telemetry,
                    data_quality=envelope.payload.get("data_quality"),
                )
                if not isinstance(raw, dict):
                    finding.notes.append(
                        "TASK payload did not contain a canonical telemetry object."
                    )
                self._last_telemetry = telemetry
                payload = finding.model_dump(mode="json")
                message_type = MsgType.FINDING
            elif envelope.type == MsgType.QUERY:
                if envelope.payload.get("action") != "upstream_neighbors":
                    return [self._error(
                        envelope, "unsupported connectivity query action"
                    )]
                payload = self._neighbor_response(envelope)
                message_type = MsgType.RESPONSE
            else:
                return [self._error(
                    envelope, "connectivity accepts TASK and QUERY envelopes only"
                )]
            return [Envelope(
                type=message_type,
                sender=self.name,
                recipient=envelope.sender,
                correlation_id=envelope.msg_id,
                payload=payload,
            )]
        except Exception as exc:  # Agent protocol boundary must never raise.
            fallback = Finding(
                agent=self.name,
                hypotheses=[Hypothesis(cause="normal", score=0.0)],
                affected_devices=[],
                evidence=[],
                data_quality=None,
                notes=[f"Connectivity analysis could not complete: {exc}"],
            )
            return [Envelope(
                type=MsgType.RESPONSE if envelope.type == MsgType.QUERY else MsgType.FINDING,
                sender=self.name,
                recipient=envelope.sender,
                correlation_id=envelope.msg_id,
                payload=fallback.model_dump(mode="json"),
            )]

    def _neighbor_response(self, envelope: Envelope) -> dict[str, Any]:
        params = envelope.payload.get("params")
        params = params if isinstance(params, dict) else {}
        device_id = params.get("device_id")
        supplied = envelope.payload.get("telemetry")
        telemetry = supplied if isinstance(supplied, dict) else self._last_telemetry
        graph, _, _ = _build_graph(telemetry.get("topology"))
        if not isinstance(device_id, str) or device_id not in graph:
            return {
                "action": "upstream_neighbors",
                "device_id": device_id,
                "upstream_neighbors": [],
                "downstream_neighbors": [],
                "upstream": [],
                "downstream": [],
                "notes": [f"device {device_id!r} is not present in the topology"],
            }
        upstream = sorted(graph.predecessors(device_id))
        downstream = sorted(graph.successors(device_id))
        return {
            "action": "upstream_neighbors",
            "device_id": device_id,
            "upstream_neighbors": upstream,
            "downstream_neighbors": downstream,
            "upstream": upstream,
            "downstream": downstream,
            "notes": [],
        }

    def _error(self, envelope: Envelope, message: str) -> Envelope:
        return Envelope(
            type=MsgType.ERROR,
            sender=self.name,
            recipient=envelope.sender,
            correlation_id=envelope.msg_id,
            payload={
                "code": "CONNECTIVITY_AGENT_ERROR",
                "message": message,
                "recoverable": True,
            },
        )
