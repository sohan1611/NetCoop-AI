# NetCoop AI — Multi-Agent Network Fault Diagnosis and Interoperability

TechZephyr 4.0 Agentic AI Hackathon entry. A team of specialised agents analyses synthetic campus-network telemetry, exchanges structured messages over a documented protocol, and produces one evidence-based diagnostic report. It states **insufficient evidence** instead of guessing when the data does not support a diagnosis.

> Status: work in progress. Protocol and normalizer are implemented; the remaining agents, orchestrator, CLI and dashboard are being added (see [docs/TASKS.md](docs/TASKS.md)).

## What it does
- Identifies the probable root cause of a network issue and the affected devices or segments.
- Supports it with telemetry evidence and agent findings, lists alternative explanations, and gives a confidence value plus category.
- Recommends practical corrective actions (simulated only; nothing touches real infrastructure).
- Tolerates incompatible field names, units, schema versions and missing sections, and reports failures clearly.

## Architecture
| Agent | Responsibility | State |
|---|---|---|
| Normalizer / Interop | Validate input, map aliases, convert units, upgrade old schemas, score data quality | done |
| Performance | Latency, jitter, loss, throughput, utilization, RSSI, retransmissions | planned |
| Infrastructure | Device status, CPU/memory, interface errors, queue drops | planned |
| Connectivity | Topology, blast radius, common upstream device, DNS/DHCP health | planned |
| Correlator | Scores competing hypotheses, resolves conflicts, queries agents for more data | planned |
| Reporter | Final JSON and Markdown report, playbook actions | planned |

Full design: [docs/PLAN.md](docs/PLAN.md).

## Agent protocol
JSON envelope `{msg_id, schema_version, type, sender, recipient, correlation_id, timestamp, payload}` with message types `TASK`, `FINDING`, `QUERY`, `RESPONSE`, `ERROR`. Every field, unit and an example per type are in [docs/PROTOCOL.md](docs/PROTOCOL.md). Models live in `netcoop/protocol.py` (pydantic v2); invalid messages are rejected, not crashed on.

## Confidence method
`match = Σ wᵢ·mᵢ / Σ wᵢ`, `coverage` = share of signature metrics present,
`confidence = match × (0.5 + 0.5·coverage) × data_quality × agreement`.
Categories: ≥0.75 high, 0.5–0.75 medium, 0.3–0.5 low, below 0.3 → `insufficient_evidence`.

## Output format
The report follows the problem statement: `incident_id`, `status`, `suspected_root_cause`, `confidence`, `affected_devices`, `supporting_evidence`, `alternative_causes`, `recommended_actions`, `evidence_sources`, `limitations`, plus `confidence_category` and `run_id`.

## Setup
Requires Python 3.11+ (developed on 3.14).
```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env    # optional: GEMINI_API_KEY / GEMINI_MODEL
```
Everything works without an LLM (`--no-llm`); Gemini is optional.

## Tests
```bash
pytest -q
```

## Scenarios
Normal operation, link congestion, weak Wi-Fi, interface fault, device overload, DNS failure, DHCP failure, renamed fields/units, legacy schema, missing sections, malformed JSON, conflicting evidence, insufficient data, plus the organizer's generator format. (Scenario files are being added.)

## Limitations
Synthetic data only; remediation is simulated; rule-based signatures are tuned for the campus topology in the scenarios.
