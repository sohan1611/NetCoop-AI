# NetCoop AI — Build Plan (TechZephyr 4.0 Agentic AI finale, 31 Oct 12:00 → 1 Nov 12:00)

## Context
The problem statement asks for a multi-agent system that diagnoses campus network faults from synthetic telemetry. It must output a structured JSON report with root cause, evidence, affected devices, confidence, alternative causes, actions and limitations, and it must say "insufficient evidence" when the data doesn't support a diagnosis. Judging is **submission-only**: GitHub repo, 3–5 min demo video, README/architecture docs, sample data + test cases, runnable prototype (PS §7). No live demo or presentation, so the repo and a clean, recordable demo flow (dashboard + CLI) are what judges see.
Scoring focus: interoperability/protocol 30%, diagnostic accuracy/evidence 25%, orchestration and conflict resolution 15%, robustness to bad data 15%, innovation 10%, code/docs 5%. The finale sheet also scores agentic autonomy, tool interaction, adaptation/failure recovery, and verification.
User decisions: **Gemini free API** (optional; everything also works rule-based), **FastAPI + HTML/JS dashboard**, and the user will drop the organizers' Colab generator `.ipynb` into `data/generator/`.

## Architecture (Python 3.14 is installed; deps: pydantic, fastapi, uvicorn, networkx, google-genai, python-dotenv, pytest)
```
netcoop/
  protocol.py      pydantic Envelope + Finding/Evidence/Query/Report models, schema_version "1.0"
  bus.py           async in-process message bus; every message appended to runs/<run_id>/messages.jsonl
  orchestrator.py  plans the DAG, fans out in parallel, timeouts/retry/degrade, follow-up loop, checkpoints
  state.py         run checkpoint (JSON) → `resume <run_id>`
  config.py        thresholds + hypothesis signatures (single source, documented)
  playbook.py      root cause → recommended actions + simulated remediation effects
  llm.py           Gemini client: env GEMINI_API_KEY/GEMINI_MODEL, 8s timeout, cache, None on failure
  agents/base.py, normalizer.py, performance.py, infrastructure.py, connectivity.py, correlator.py, reporter.py
  cli.py, __main__.py
  api/server.py + api/static/{index.html,app.js,style.css}
  mcp_server.py    (stretch) exposes telemetry/diagnose as MCP tools
data/scenarios/*.json (+ expected labels), data/generator/ (organizer notebook)
scripts/gen_scenarios.py, scripts/evaluate.py; tests/; docs/{PLAN,TASKS,PROTOCOL}.md
```

**Protocol (the 30% criterion).** Envelope `{msg_id, schema_version, type: TASK|FINDING|QUERY|RESPONSE|ERROR, sender, recipient, correlation_id, timestamp, payload}`. A Finding is `{agent, hypotheses:[{cause, score}], affected_devices, evidence:[{metric, value, unit, threshold, device_id, timestamp, interpretation}], data_quality, notes}`. Units are canonical (ms, %, Mbps, dBm). All of it is documented in `docs/PROTOCOL.md`. The bus validates every message and turns an invalid one into an ERROR message instead of crashing.

**Agents**
1. **Normalizer / Interop**: validates input; maps field aliases (`lat`, `latency`, `rtt_ms` → `latency_ms`), converts units (s→ms, kbps→Mbps, 0–1→%) and upgrades older schema versions. It flags missing, stale, out-of-range and duplicate values and emits a `data_quality` score. Gemini maps only fields it doesn't recognise, and each mapping carries a confidence; without Gemini, unknown fields are reported and dropped.
2. **Performance**: checks latency, jitter, loss, throughput and utilization, plus RSSI and retransmissions, against thresholds and recent baseline.
3. **Infrastructure**: checks device status, CPU/memory, interface errors, queue drops and down/flapping devices.
4. **Connectivity**: builds the networkx topology; finds the common upstream device of the affected nodes, the blast radius and isolated segments; checks DNS/DHCP service health.
5. **Correlator**: scores hypotheses (normal, congestion, weak_wifi, interface_fault, device_overload, dns_failure, dhcp_failure, hardware_failure, latency_degradation) against signatures in `config.py`, using **all** agents' findings. It resolves conflicts by weight × agent agreement. **Adaptive loop:** if confidence < 0.5 or the top-2 margin < 0.15, it sends QUERY messages to specific agents (widen the time window, inspect upstream neighbours, per-interface detail), at most 2 rounds. It returns `insufficient_evidence` when coverage stays low.
6. **Reporter**: builds the JSON report exactly in the PS shape, plus Markdown. Actions come from the playbook. An optional Gemini narrative summary may never change the cause or confidence.

**Confidence method (documented in README).** `match = Σ wᵢ·mᵢ / Σ wᵢ` (mᵢ = graded 0–1 match of each signature metric), `coverage` = share of signature metrics present, `conf = match × (0.5 + 0.5·coverage) × dq_factor × agreement_factor`. Categories: ≥0.75 high, 0.5–0.75 medium, 0.3–0.5 low, below that → status `insufficient_evidence`.

**Robustness and recovery.** Malformed JSON, missing sections, wrong types and unknown schema versions produce a clear error report, never a crash. An agent exception or timeout is retried once; after that the run continues degraded and the agent is listed in `limitations`. A fault-injection flag (`--fail-agent infrastructure`) demonstrates this.

**Verification loop (bonus).** The dashboard/CLI can apply a *simulated* remediation from the playbook, re-run the diagnosis on the patched telemetry, and report whether the symptoms cleared. Human approval is required first (HITL).

**Scenarios (each with an expected label):** normal, congestion, weak_wifi, interface_fault, device_overload, dns_failure, dhcp_failure, interop_renamed_fields_units, schema_v0_legacy, missing_sections, malformed_json, conflicting_evidence, insufficient_data.

## Tasks for Codex (Codex model: GPT-6.1 Sol; effort in brackets)
| ID | Task | Effort | Milestone |
|---|---|---|---|
| T1 | Scaffold: pyproject/requirements, .gitignore (runs/, .env, *.pdf, Plan.txt), .env.example, protocol.py, docs/PROTOCOL.md | medium | M1 |
| T2 | Normalizer agent + alias/unit/version tables + data-quality scoring | medium | M1 |
| T3 | scripts/gen_scenarios.py + 13 scenario files + expected labels (match organizer format once the notebook arrives) | low | M1 |
| T4 | Performance + Infrastructure agents | medium | M1 |
| T5 | Connectivity agent (networkx, upstream root, DNS/DHCP) | medium | M1 |
| T6 | bus.py + orchestrator.py + state.py (parallel, timeouts, retry, degrade, JSONL log, resume) | high | M1 |
| T7 | Correlator (signatures, scoring, conflicts, QUERY loop, insufficient evidence) | high | M1 |
| T8 | Reporter + playbook (JSON + Markdown) | low | M1 |
| T9 | CLI: `diagnose <file> [--fail-agent X] [--no-llm]`, `evaluate`, `resume <run_id>` | low | M1 |
| T10 | llm.py (Gemini) + LLM field-mapping in normalizer + narrative in reporter | medium | M2 |
| T11 | scripts/evaluate.py (accuracy table vs. labels) + pytest suite (protocol, normalizer, each scenario, failure injection) | medium | M2 |
| T12 | FastAPI + dashboard: upload/select scenario, live agent messages (SSE), topology view, report, approve simulated remediation → verify | medium | M2 |
| T13 | MCP server (stretch) | medium | M3 |
| T14 | README: architecture diagram, agents, protocol, confidence method, error handling, setup, how to run tests | low (or Claude C5) | M3 |
Order: T1 → (T2, T3) → (T4, T5) → T6 → T7 → T8 → T9 = **M1: CLI end-to-end**. Then T10–T12 = **M2**, T13–T14 = **M3**. Feature freeze at ~H17.

## Workflow and model switches
- **Now (this session, C1 Opus/high):** after approval I write `docs/PLAN.md` (this architecture), `docs/TASKS.md` (the task table with status and acceptance checks), and update the Project section of CLAUDE.md/AGENTS.md. Then: **"→ NEW SESSION now on C2 (Sonnet 5.5 / medium)"**, which starts the Codex loop at T1.
- Build loop (C2): I run one `codex exec` per task and review `git diff --stat` plus the risky files. You commit; I never add trailers.
- **→ C4 (Opus 5.5 / medium)** if T6 or T7 fails review twice. **→ C3 (Sonnet / low)** for review-fix rounds. **→ NEW SESSION** at M1 done (S3), M2 done (S4), freeze (S5); **C6** final review (~H20); **C5 Haiku** README polish (~H21).
- When the Colab notebook lands in `data/generator/`: I read only its generation function and adjust T3 and the normalizer aliases.

## Verification
- `python -m netcoop diagnose data/scenarios/congestion.json --no-llm` prints a valid JSON report naming congestion and the right device.
- `python scripts/evaluate.py` shows ≥ 12/13 scenarios correct (malformed → clear error, insufficient → `insufficient_evidence`).
- `pytest` passes; `--fail-agent infrastructure` still produces a report with the limitation listed.
- `uvicorn netcoop.api.server:app` dashboard runs a scenario end-to-end with live messages.
- `git log --format='%an%n%b'` shows only Sohan Mandal and no trailers.
