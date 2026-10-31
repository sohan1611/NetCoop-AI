# Demo script (target 4:30, hard limit 5:00)
Setup before recording: `uvicorn api.server:app`, browser at http://127.0.0.1:8000, a terminal in the repo with the venv active, `runs/` cleared.

| Time | Screen | Say / do |
|---|---|---|
| 0:00–0:25 | README title + mermaid diagram | "Campus faults show up as overlapping symptoms in messy telemetry. NetCoop AI uses six agents that talk over a documented JSON protocol and produce one evidence-based report. Everything runs offline; the LLM is optional." |
| 0:25–1:30 | Dashboard: pick `congestion`, Run | Watch the live timeline: normalizer TASK → performance / infrastructure / connectivity FINDINGs in parallel. Point at a QUERY → RESPONSE pair: "confidence was below threshold, so the correlator asked an agent for more data (max 2 rounds)." |
| 1:30–2:15 | Report card + topology | Root cause, confidence (~0.87, high), affected devices highlighted in the graph, supporting evidence with metric/value/threshold, alternative causes, actions, limitations. "Evidence traces back to the agent that produced it." |
| 2:15–3:00 | Dashboard: run `interop_renamed_fields_units`, then `malformed_json` | Renamed fields and unit mismatches are normalised with a data-quality score; malformed input returns a clear error, no crash. Optionally `org_latency_spike` to show the organizer's format is accepted. |
| 3:00–3:30 | Dashboard: `dns_failure` with fail-agent `infrastructure` (or CLI: `python -m netcoop diagnose data/scenarios/congestion.json --no-llm --fail-agent infrastructure`) | "One agent is killed; the run still finishes, lowers confidence and records the limitation." |
| 3:30–4:00 | Report card → "Approve simulated fix" | Simulated remediation patches the telemetry, re-diagnoses, shows before → after and `verified: true`. "Nothing touches real infrastructure." |
| 4:00–4:20 | `insufficient_data` run | "When evidence is thin it says insufficient evidence instead of guessing." |
| 4:20–4:50 | Terminal: `python -m netcoop evaluate --no-llm` then `python -m pytest -q` | Show the 16/16 table and passing tests. "Rules, thresholds and the confidence formula are in the README." |
| 4:50–5:00 | README setup block | "Clone, `pip install -e .`, run. Thanks." |

Cut list if over time: skip `org_latency_spike`, shorten the pytest run, drop the insufficient_data run to a single sentence.
