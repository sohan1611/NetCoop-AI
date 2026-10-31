# Demo script (target 4:30, hard limit 4:45)
**Before recording**
- Local (recommended, no cold start): `uvicorn api.server:app`, browser at http://127.0.0.1:8000, 1280×800 window, zoom 100 %.
- Live alternative: open https://netcoop-ai.vercel.app/api/health 2 minutes before you start (wakes the Render backend, ~50 s from cold) and reload the console once you see `"status"`.
- Terminal in the repo with the venv active; README open in a second tab. Close other tabs and notifications.
- Each run takes about 1 s locally (`org_hardware_failure` about 4 s), so no waiting is scripted.

| Time | Screen | Do | Say |
|---|---|---|---|
| 0:00–0:20 | README: title + mermaid diagram | Scroll slowly over the diagram. | "Campus faults show up as overlapping symptoms in messy telemetry. NetCoop AI is a swarm of six agents that talk over a documented JSON protocol and agree on one evidence-based report. It runs fully offline; the LLM is optional." |
| 0:20–1:00 | Console, idle | Click **Trigger Swarm Diagnostic Run** (congestion). Point at the pipeline pills and the Agent Inter-Communication Bus as they fill. | "The normalizer cleans the input, then performance, infrastructure and connectivity analyse it in parallel. Every message on the bus is a validated envelope: task, finding, query, response. The correlator weighs their findings." |
| 1:00–1:50 | Same run: report | Scroll the report: root cause, Limitations (open it), affected device, evidence tiles, telemetry table, hypothesis bars, actions. | "Congestion at 94 %, high confidence, on the core router. Each evidence tile shows the value, its baseline and the device it came from, and the table maps raw fields to normalized metrics. The connectivity agent preferred latency degradation; the correlator set that aside and says why in the limitations, instead of hiding the disagreement." |
| 1:50–2:10 | Topology tab | Click **Topology**, click the core router node. | "The topology view marks affected and upstream devices; clicking a node shows its metrics." |
| 2:10–2:55 | Spawn Agent Run modal | Run `interop_renamed_fields_units`; point at the **Format Normalization** chip. Then run `malformed_json`. | "Interoperability: this file renames fields and uses different units. The normalizer maps and converts them and still finds congestion. It also accepts legacy schemas and the organizers' own format. Broken JSON is rejected with a clear reason, not a crash." |
| 2:55–3:25 | Modal | Run `dns_failure` with fault injection **infrastructure**. Open Limitations. | "Now I kill the infrastructure agent mid-run. The orchestrator times it out, records the limitation and the remaining agents still pin the DNS failure." |
| 3:25–3:45 | Modal | Run `insufficient_data`. | "When the evidence is thin it says insufficient evidence, 6 %, instead of guessing." |
| 3:45–4:10 | Incidents tab → congestion row → actions | Click the congestion incident, scroll to actions, **Apply Simulated Remediation** → confirm. | "Playbook actions are simulated on a copy of the telemetry, then the swarm re-diagnoses: congestion to normal, verified. Nothing touches real infrastructure." |
| 4:10–4:20 | Export menu | **Export Root-Cause Report** → **Print / Save PDF**, show the preview, cancel. | "The report exports as JSON, Markdown or a clean PDF." |
| 4:20–4:40 | Terminal | `python -m netcoop evaluate --no-llm` (then `python -m pytest -q` if time allows). | "Sixteen labelled scenarios, sixteen correct, and 198 tests. Setup, protocol and the confidence formula are in the README. Thanks." |

**Cut list if over time:** skip the Topology node click, drop `malformed_json` to a sentence, skip pytest (keep `evaluate`).
**Optional extra (only if under 4:15):** run `org_hardware_failure` to show the organizer format: "2 formats reconciled", hardware failure on SW_CAMPUS_2.
