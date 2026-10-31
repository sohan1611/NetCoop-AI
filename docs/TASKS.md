# TASKS — NetCoop AI
Status: TODO / WIP / DONE / BLOCKED. Codex: GPT-6.1 Sol (effort in brackets). Read docs/PLAN.md for architecture.
Global rules: Python 3.14, type hints, pydantic v2 models, no network calls in tests, everything runs with `--no-llm`.
Every task: add/extend pytest tests in tests/ for what it builds; `pytest -q` must pass.

| ID | Status | Effort | Task | Acceptance check |
|---|---|---|---|---|
| T1 | DONE | medium | Scaffold: `pyproject.toml` (deps: pydantic, fastapi, uvicorn, networkx, google-genai, python-dotenv, pytest), `.gitignore` (runs/, .env, __pycache__, *.pdf, Plan.txt, .venv), `.env.example` (GEMINI_API_KEY=, GEMINI_MODEL=), `netcoop/__init__.py`, `netcoop/protocol.py` (Envelope, MsgType enum, Evidence, Hypothesis, Finding, Query, DataQuality, Report — Report fields exactly as PS §6 plus `confidence_category`, `run_id`), `docs/PROTOCOL.md` (every field, meaning, unit, example message per type) | `pip install -e .` works; `pytest -q` round-trips each model to/from JSON and rejects a bad envelope |
| T2 | DONE | medium | `netcoop/agents/base.py` (Agent ABC: `name`, `async handle(envelope)->list[Envelope]`) + `netcoop/agents/normalizer.py`: alias table, unit conversion, schema version upgrade (v0 legacy → 1.0), validation (missing/stale/out-of-range/duplicate/wrong type), `data_quality` {score 0–1, issues[]}. Canonical input format in PLAN.md "Canonical telemetry" note below | renamed-field + unit-mismatched input normalizes to canonical; malformed input → ERROR envelope with clear reason, no exception |
| T3 | DONE | low | `scripts/gen_scenarios.py` (seeded, deterministic) → `data/scenarios/<name>.json` for the 13 scenarios in PLAN.md + `data/scenarios/labels.json` {name: expected_cause or "insufficient_evidence" or "error"}. Topology: core router → 2 dist switches → hostel AP zone + dept ethernet + lecture hall; DNS + DHCP servers | running script regenerates identical files |
| T4 | DONE | medium | `agents/performance.py`, `agents/infrastructure.py`: threshold + baseline checks from `netcoop/config.py`; emit FINDING with evidence (metric, value, unit, threshold, device_id, interpretation) and hypothesis scores; answer QUERY (`widen_window`, `device_detail`) | unit tests on congestion / weak_wifi / interface_fault / device_overload inputs |
| T5 | DONE | medium | `agents/connectivity.py`: networkx graph; affected-node set → common upstream ancestor, blast radius, isolated segments; DNS/DHCP service checks; answer QUERY `upstream_neighbors` | dns_failure + interface_fault scenarios point to correct device/service |
| T6 | TODO | high | `bus.py` (async pub/sub, validates every envelope, invalid → ERROR, logs JSONL to runs/<run_id>/messages.jsonl), `orchestrator.py` (normalize → parallel analysis agents → correlator ↔ QUERY loop ≤2 rounds → reporter; per-agent timeout 10s, 1 retry, degrade + record limitation; `fail_agents` injection), `state.py` (checkpoint after each stage; resume) | killing infrastructure agent still yields report with limitation; resume from checkpoint completes |
| T7 | TODO | high | `agents/correlator.py` + signatures in `config.py`: confidence formula from PLAN.md, agreement factor across agents, conflict resolution, alternatives = next hypotheses within 0.25, QUERY when conf<0.5 or margin<0.15, `insufficient_evidence` when below 0.3 / coverage<0.4 | conflicting_evidence + insufficient_data scenarios behave per labels |
| T8 | TODO | low | `agents/reporter.py` + `playbook.py`: PS §6 JSON + Markdown (`runs/<id>/report.json|md`), actions per cause, evidence_sources, limitations; `simulate_remediation(cause, telemetry)` returns patched telemetry | report validates against `Report` model |
| T9 | TODO | low | `netcoop/cli.py` + `__main__.py`: `diagnose <file> [--fail-agent X] [--no-llm] [--md]`, `evaluate`, `resume <run_id>`, `remediate <run_id> --approve` (simulate + re-diagnose + verified true/false) | commands in PLAN.md Verification run |
| T10 | TODO | medium | `llm.py` Gemini client (timeout 8s, disk cache, returns None on any failure) ; normalizer uses it only for unknown fields (with confidence, logged); reporter adds `narrative` | with no key everything still passes |
| T11 | TODO | medium | `scripts/evaluate.py` (table: scenario, expected, got, confidence, pass) + full pytest suite | ≥12/13 pass |
| T12 | TODO | medium | `api/server.py` (FastAPI: list scenarios, upload, run, SSE stream of bus messages, report, remediate) + `api/static/` single page: scenario picker/upload, live agent message timeline, topology (cytoscape.js from CDN) with affected nodes highlighted, report card, "Approve simulated fix" button | manual run of 3 scenarios in browser |
| T13 | TODO | medium | `mcp_server.py` (stretch): tools `list_scenarios`, `get_telemetry`, `diagnose` | MCP inspector lists tools |
| T14 | TODO | low | README.md: problem, architecture diagram (mermaid), agents, protocol, confidence method, error handling, setup, run, tests, scenarios, limitations, third-party acknowledgements | fresh clone setup works |
| T15 | TODO | — (Claude C5) | `docs/DEMO_SCRIPT.md`: 3–5 min video script — dashboard run of congestion → live agent messages → QUERY loop → report; interop/malformed input handled; `--fail-agent` degrade; simulated fix + verify; `evaluate.py` table | script fits ≤5 min |

## Canonical telemetry (input after normalization)
```json
{"schema_version":"1.0","incident_id":"INC-0001","collected_at":"ISO8601",
 "topology":{"nodes":[{"id":"CORE_RTR_01","type":"router","role":"core","location":"DC"}],
             "links":[{"src":"CORE_RTR_01","dst":"HOSTEL_DIST_SW_01","capacity_mbps":1000}]},
 "devices":[{"device_id":"...","type":"switch|router|ap|server|host","status":"up|down|degraded",
             "cpu_pct":0,"mem_pct":0,"interface_errors":0,"queue_drops":0}],
 "samples":[{"device_id":"...","timestamp":"ISO8601","latency_ms":0,"jitter_ms":0,"packet_loss_pct":0,
             "throughput_mbps":0,"bandwidth_mbps":0,"link_utilization_pct":0,"rssi_dbm":null,"retransmission_pct":null}],
 "services":[{"name":"dns|dhcp","server_id":"...","status":"up|down|degraded","success_rate_pct":100,"response_ms":0}],
 "symptoms":["free-text user complaints, optional"]}
```
## Organizer format (data/generator/Mock_data_builder_netScoopAI.ipynb) — MUST be accepted as input
Single object or list of objects: `{"timestamp","device_id":"SW_CAMPUS_<n>","metrics":{"cpu_utilization_pct","link_utilization_pct","packet_loss_pct","latency_ms","jitter_ms","queue_drops","interface_errors"},"status":"Healthy|Warning|Critical"}`; fault types: normal, latency_spike, hardware_failure. No topology, no services.
Normalizer maps: cpu_utilization_pct→cpu_pct; metrics→device + sample rows; status Healthy→up, Warning→degraded, Critical→degraded + severity critical. Missing topology → connectivity agent reports limitation "no topology supplied", never crashes.
T3 also: copy their function into `data/generator/organizer_generator.py` (seeded) and emit `org_normal.json`, `org_latency_spike.json`, `org_hardware_failure.json`. Hypothesis list gains `latency_degradation` and `hardware_failure` (replaces hardware_down).
Never change the canonical format; adapt in the normalizer.

## Session log (≤10 lines per session)
- S1 (Opus/high): problem read, PLAN + TASKS written. Next: T1. Organizer notebook read; format rules added above.
