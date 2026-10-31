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
| T6 | DONE | high | `bus.py` (async pub/sub, validates every envelope, invalid → ERROR, logs JSONL to runs/<run_id>/messages.jsonl), `orchestrator.py` (normalize → parallel analysis agents → correlator ↔ QUERY loop ≤2 rounds → reporter; per-agent timeout 10s, 1 retry, degrade + record limitation; `fail_agents` injection), `state.py` (checkpoint after each stage; resume) | killing infrastructure agent still yields report with limitation; resume from checkpoint completes |
| T7 | DONE | high | `agents/correlator.py` + signatures in `config.py`: confidence formula from PLAN.md, agreement factor across agents, conflict resolution, alternatives = next hypotheses within 0.25, QUERY when conf<0.5 or margin<0.15, `insufficient_evidence` when below 0.3 / coverage<0.4 | conflicting_evidence + insufficient_data scenarios behave per labels |
| T8 | DONE | low | `agents/reporter.py` + `playbook.py`: PS §6 JSON + Markdown (`runs/<id>/report.json|md`), actions per cause, evidence_sources, limitations; `simulate_remediation(cause, telemetry)` returns patched telemetry | report validates against `Report` model |
| T9 | DONE | low | `netcoop/cli.py` + `__main__.py`: `diagnose <file> [--fail-agent X] [--no-llm] [--md]`, `evaluate`, `resume <run_id>`, `remediate <run_id> --approve` (simulate + re-diagnose + verified true/false) | commands in PLAN.md Verification run |
| T10 | DONE | medium | `llm.py` Gemini client (timeout 8s, disk cache, returns None on any failure) ; normalizer uses it only for unknown fields (with confidence, logged); reporter adds `narrative` | with no key everything still passes |
| T11 | DONE | medium | `scripts/evaluate.py` (table: scenario, expected, got, confidence, pass) + full pytest suite | ≥12/13 pass |
| T12 | DONE | medium | `api/server.py` (FastAPI: list scenarios, upload, run, SSE stream of bus messages, report, remediate) + `api/static/` single page: scenario picker/upload, live agent message timeline, topology (cytoscape.js from CDN) with affected nodes highlighted, report card, "Approve simulated fix" button | manual run of 3 scenarios in browser |
| T13 | DONE | medium | `mcp_server.py` (stretch): tools `list_scenarios`, `get_telemetry`, `diagnose` | MCP inspector lists tools |
| T14 | DONE | low | README.md: problem, architecture diagram (mermaid), agents, protocol, confidence method, error handling, setup, run, tests, scenarios, limitations, third-party acknowledgements | fresh clone setup works |
| T15 | DONE | — (Claude C5) | `docs/DEMO_SCRIPT.md`: 3–5 min video script — dashboard run of congestion → live agent messages → QUERY loop → report; interop/malformed input handled; `--fail-agent` degrade; simulated fix + verify; `evaluate.py` table | script fits ≤5 min |
| T16 | TODO | medium | Fix review bugs B1–B8 below (S4 review). Owner: Sonnet until Codex quota resets | `pytest -q` passes, `evaluate` 16/16, `python scripts/stress_test.py` shows no CRASH and B2–B7 cases diagnosed correctly |

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

## Review bugs (S4, Opus review) — fix in order, add a regression test for each
Repro tool: `python scripts/stress_test.py`.
- **B1 HIGH** — correlator or reporter failure crashes CLI/evaluate/MCP with `KeyError: 'reporter'` (`netcoop/cli.py:34` `_report`); dashboard shows `error` instead of a degraded diagnosis. README promises no exceptions. Fix in `orchestrator.py`: if correlator fails → build a fallback diagnosis (status `insufficient_evidence`, affected devices + evidence merged from analysis findings, limitation `correlator unavailable: …`); if reporter fails → orchestrator builds a minimal valid `Report` itself and stores it in `state.findings['reporter']`. `_report` must never KeyError. Test: `--fail-agent correlator` and `--fail-agent reporter` exit 0 with a valid report.
- **B2 HIGH** — `hardware_failure` signature puts 0.50 weight on `status` (`config.py` SIGNATURES). Organizer hardware_failure record with status removed or unknown (`"Purple"`) → diagnosed `congestion`. Make metrics carry the decision: add `packet_loss_pct`, `queue_drops`, `latency_ms` (or a "multi-domain severe" rule: ≥3 domains at crit on one device → hardware_failure); reduce status weight to ≤0.25. Keep all 16 labels passing.
- **B3 MED** — numeric strings (`"99.9"`) flagged wrong_type and dropped → insufficient_evidence. Normalizer: coerce numeric strings (strip `%`, `ms`, units) to float, record an info issue, don't drop.
- **B4 MED** — organizer record without `device_id` not recognised (fields dropped as unknown). Detect organizer format by `metrics` key alone; default `device_id` = `UNKNOWN_DEVICE_<index>` + data-quality issue.
- **B5 MED** — healthy organizer record → `normal` at 0.432 (low). Missing topology/services is expected for organizer format: don't count those as dq penalties when input format is organizer (still list as limitation). Target ≥0.7 for clean org_normal.
- **B6 MED** — without LLM, CamelCase/other-style names (`CpuUtilization`, `LatencyMs`, `PacketLoss`, `QueueDrops`, `InterfaceErrors`, `LinkUtilization`) dropped. Before alias lookup: camel→snake, lowercase, strip `_pct/_ms` suffix variants; then alias table. Stress case `camel_fields` must diagnose hardware_failure.
- **B7 LOW** — wrappers not unwrapped: `{"records"|"data"|"telemetry"|"items": [...]}` → treat inner list as input.
- **B8 LOW** — empty input limitation reads "Leading hypotheses are close: normal=0.000…"; replace with "no usable telemetry samples supplied" when there is no evidence. Flag physically impossible values (latency > 60000 ms, counts > 1e9, pct > 100) as out-of-range.
- **Note (not a bug fix, optional)** — rules are edge-tuned: ±15 % value jitter drops weak_wifi/congestion to 8/10. If time permits, widen grading so near-threshold values give partial credit; never break labels.
After B1–B8: update README robustness section if behaviour changed, rerun stress test, commit per bug group.

## Session log (≤10 lines per session)
- S1 (Opus/high): problem read, PLAN + TASKS written. Next: T1. Organizer notebook read; format rules added above.
- S2 (Sonnet/medium): T1–T9 DONE via Codex gpt-6.1-sol (T1–T7 ran at xhigh by mistake; T8–T9 low). M1 CLI cleared: `evaluate` 16/16, 85 tests. Next: T10 (llm.py, medium), T11 (evaluate script + suite, medium), T12 (API + dashboard, medium). Known nit: duplicate latency_ms evidence rows in report (dedupe in T11/T12). Codex invoke: `-m gpt-6.1-sol -c model_reasoning_effort=<per Effort col>`, run_in_background.
- S2 cont.: T10, T11 DONE via Codex (reviewed). Codex quota ran out during T12 (resets 5:24 PM); T12 written by Claude: `api/server.py` + `api/static/`, driven in browser (congestion, dns_failure, malformed_json, --fail-agent, simulated fix verified). Fixed T2 bug: null optional metrics were flagged wrong_type (data_quality 0.04 -> 1.0). M2 = T12. Next: T13 (MCP, stretch), T14 README, T15 demo script. Run UI: `uvicorn api.server:app`.
- S3 (Sonnet): T13 (MCP via `mcp` 2.x `MCPServer`, optional dep `.[mcp]`), T14 README, T15 demo script all DONE, written by Claude (Codex down). 125 tests, evaluate 16/16. Next: record demo video, final repo check/submission.
- S4 (Opus review): T1–T15 verified (125 tests, evaluate 16/16). Stress test found B1–B8 (see Review bugs). Next: T16 by Sonnet (Codex quota out).
