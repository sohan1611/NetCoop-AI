# NetCoop message protocol

All agent traffic uses schema version `1.0` and an `Envelope`. Canonical units are `ms` for latency, jitter, and response time; `%` for loss and utilization; `Mbps` for throughput and bandwidth; and `dBm` for RSSI. Timestamps are ISO 8601 strings in UTC.

## Envelope and message types

| Field | Meaning | Unit |
|---|---|---|
| `msg_id` | UUID identifying this message; generated when omitted. | none |
| `schema_version` | Protocol version; must be `1.0`. | none |
| `type` | One of `TASK`, `FINDING`, `QUERY`, `RESPONSE`, or `ERROR`. | none |
| `sender` | Agent or component that emitted the message. | none |
| `recipient` | Intended agent or component. | none |
| `correlation_id` | Optional ID of the task/query this message answers. | none |
| `timestamp` | Message creation time; generated in UTC when omitted. | ISO 8601 |
| `payload` | Type-specific JSON object. | none |

`TASK` assigns work, `FINDING` carries an agent's analysis, `QUERY` asks for more evidence, `RESPONSE` answers a query or task, and `ERROR` reports a failure without crashing the run.

### TASK

```json
{"msg_id":"9148a2ef-fef1-469e-93b3-84156cb7d7e0","schema_version":"1.0","type":"TASK","sender":"orchestrator","recipient":"performance","correlation_id":"INC-0001","timestamp":"2026-10-31T08:30:00+00:00","payload":{"incident_id":"INC-0001","action":"analyze","telemetry_ref":"normalized.json"}}
```

### FINDING

```json
{"msg_id":"f13d9149-7097-4583-a2a8-961f89e1465f","schema_version":"1.0","type":"FINDING","sender":"performance","recipient":"correlator","correlation_id":"INC-0001","timestamp":"2026-10-31T08:30:02+00:00","payload":{"agent":"performance","hypotheses":[{"cause":"congestion","score":0.82}],"affected_devices":["AP-01"],"evidence":[{"metric":"latency","value":180,"unit":"ms","threshold":100,"device_id":"AP-01","timestamp":"2026-10-31T08:29:00+00:00","interpretation":"Latency exceeds the threshold."}],"data_quality":{"score":0.9,"issues":["One stale sample omitted."]},"notes":["Sustained for five minutes."]}}
```

### QUERY

```json
{"msg_id":"fd05c698-a6a7-441e-93ac-1f3c02f21f94","schema_version":"1.0","type":"QUERY","sender":"correlator","recipient":"performance","correlation_id":"INC-0001","timestamp":"2026-10-31T08:30:03+00:00","payload":{"target_agent":"performance","action":"widen_window","params":{"minutes":15},"reason":"Top hypotheses are too close."}}
```

### RESPONSE

```json
{"msg_id":"9caf4d79-1170-427f-b68a-737fdad67279","schema_version":"1.0","type":"RESPONSE","sender":"performance","recipient":"correlator","correlation_id":"fd05c698-a6a7-441e-93ac-1f3c02f21f94","timestamp":"2026-10-31T08:30:04+00:00","payload":{"action":"widen_window","status":"completed","result":{"window_minutes":15,"sample_count":45}}}
```

### ERROR

```json
{"msg_id":"f5b31771-9c6d-4e99-9eb1-3518a69f160b","schema_version":"1.0","type":"ERROR","sender":"bus","recipient":"orchestrator","correlation_id":"INC-0001","timestamp":"2026-10-31T08:30:05+00:00","payload":{"code":"INVALID_ENVELOPE","message":"sender is required","recoverable":true}}
```

## Payload models

### Evidence

| Field | Meaning | Unit |
|---|---|---|
| `metric` | Canonical metric name. | none |
| `value` | Observed numeric or text value. | metric-specific |
| `unit` | Optional canonical unit. | `ms`, `%`, `Mbps`, `dBm`, or none |
| `threshold` | Optional comparison threshold in the same unit as `value`. | metric-specific |
| `device_id` | Optional device associated with the observation. | none |
| `timestamp` | Optional observation time. | ISO 8601 |
| `interpretation` | Plain-language significance of the observation. | none |

### Hypothesis, DataQuality, Finding, and Query

| Model.field | Meaning | Unit |
|---|---|---|
| `Hypothesis.cause` | Candidate root-cause label. | none |
| `Hypothesis.score` | Agent-local support for the cause, from 0 to 1. | ratio |
| `DataQuality.score` | Input quality from 0 to 1. | ratio |
| `DataQuality.issues` | Missing, stale, invalid, or transformed-data notes. | none |
| `Finding.agent` | Agent that produced the finding. | none |
| `Finding.hypotheses` | Ranked hypothesis objects. | none |
| `Finding.affected_devices` | Device IDs implicated by the finding. | none |
| `Finding.evidence` | Evidence objects supporting the hypotheses. | none |
| `Finding.data_quality` | Optional data-quality assessment. | none |
| `Finding.notes` | Additional analysis notes. | none |
| `Query.target_agent` | Agent expected to answer. | none |
| `Query.action` | Requested operation, such as `widen_window`, `device_detail`, or `upstream_neighbors`. | none |
| `Query.params` | Action-specific parameters. | action-specific |
| `Query.reason` | Why more evidence is needed. | none |

## Report

| Field | Meaning | Unit |
|---|---|---|
| `incident_id` | Input incident identifier. | none |
| `status` | `diagnosed`, `insufficient_evidence`, or `error`. | none |
| `suspected_root_cause` | Selected cause, or `null` when none is supportable. | none |
| `confidence` | Root-cause confidence from 0 to 1. | ratio |
| `confidence_category` | `high`, `medium`, `low`, or `insufficient`. | none |
| `severity` | Optional observed incident severity: `critical` or `warning`, independent of confidence. Defaults to `null` for normal, insufficient evidence, and error reports. Critical means an affected device has a signature metric at its critical threshold (inverted for RSSI/service success), an unambiguous down status, or a failed hosted service; otherwise a diagnosed fault is warning. UI contract: `report.severity`. | none |
| `affected_devices` | Device IDs affected by the incident. | none |
| `supporting_evidence` | Evidence summaries included in the final report. | none |
| `supporting_evidence[].metric` | Canonical metric name. | none |
| `supporting_evidence[].value` | Observed numeric or text value. | metric-specific |
| `supporting_evidence[].interpretation` | Plain-language significance of the value. | none |
| `alternative_causes` | Other plausible cause labels. | none |
| `recommended_actions` | Operator actions suggested by the playbook. | none |
| `evidence_sources` | Agents or inputs that supplied evidence. | none |
| `limitations` | Missing evidence, failed agents, or other caveats. | none |
| `run_id` | Diagnosis-run identifier. | none |

```json
{"incident_id":"INC-0001","status":"diagnosed","suspected_root_cause":"congestion","confidence":0.82,"confidence_category":"high","affected_devices":["AP-01"],"supporting_evidence":[{"metric":"latency","value":180,"interpretation":"Latency exceeds 100 ms."}],"alternative_causes":["device_overload"],"recommended_actions":["Reduce traffic on the uplink."],"evidence_sources":["performance","connectivity"],"limitations":[],"run_id":"RUN-0001"}
```
