# UI fixes — T19 Groups 1–4 review (S10, Opus)
Tested locally at 1280px and 375px with congestion, malformed_json, insufficient_data and the `fail` console command. No console errors, no page overflow (the table scrolls inside its container), and no `innerHTML` sinks.
Fix all of these as **Group 6**, after Group 5. Every fix must keep the "all values from API data" rule.

## Wrong state / logic (fix first)
- **F1 — `fail <agent>` console command has no effect.** After `fail infrastructure` then `run insufficient_data`, infrastructure still shows Complete and "5 Active Diagnostic Agents". The command must set `fail_agent` on the next `/api/run` (and echo "next run will inject failure into infrastructure"); the card must then show **Failed**.
- **F2 — the error run (malformed_json) shows false states.**
  - Mesh health reads "Critical" → show grey "Unknown — input rejected".
  - Performance, infrastructure and connectivity show **Failed** → **Skipped** (grey); they never ran.
  - The correlator shows **Complete** "Concluded None at 0%" → **Skipped**.
  - "PIPELINE CONSENSUS: SYNCHRONIZED (1/5)" → "HALTED (input rejected)".
  - "Active Incident: unknown · P3" → "—", no badge.
  - "Trigger Window: — – —" → hide the row.
  - "Primary Hypothesis: error on — — 0%" → hide the hypothesis section and show one line: "No hypothesis — input rejected by normalizer".
  - "SWARM CONSENSUS 0% · 1/5 Agents in Accord" → "—".
  - "ERROR / error" → "INPUT REJECTED".
- **F3 — insufficient_evidence run:** the root-cause quote shows "—". Show the report's `suspected_root_cause` text plus the top 3 limitations. Fix the "INSUFFICIENT EVIDENCE / insufficient evidence" duplication.
- **F4 — consensus labels disagree.** The pipeline shows "DIVERGENT (5/5)" while the tile shows "4/5 Agents in Accord" (it counts the normalizer, which has no hypothesis). Count only the agents that voted (performance, infrastructure, connectivity): SYNCHRONIZED = all agree, RESOLVED = majority agree, DIVERGENT = no majority. Use the same k/n in both places.
- **F5 — "Format Normalization: 1 conflicting formats (canonical v1)".** With one format and no conversions, show "Format Normalization: canonical v1 · no conflicts". Otherwise show "<k> formats reconciled (<list>) · <m> fields converted". Get the plural right.

## Human-readable text
- **F6** — raw enum names are shown everywhere (`insufficient_evidence`, `latency_degradation`, `device_overload`, "core", "LINK UTILIZATION PCT", "LATENCY MS", "275 count").
  - Add a `LABELS` map in app.js: causes → "Insufficient evidence", "Latency degradation", …
  - Metrics → "Link Utilization", "Round-Trip Latency", "Queue Drops", "Packet Loss Rate", "Jitter", "CPU", "Memory", "Interface Errors", "Wi-Fi RSSI", "Retransmissions".
  - Roles go uppercase in the reference style ("CORE ROUTER").
  - Drop the unit "count" and show "275".
  - Confidence category is capitalised ("High Confidence").
- **F7 — bus messages.** Sender/recipient use display names like the reference (`[PerformanceAgent → Orchestrator]`). Summaries:
  - TASK to an analysis agent → "analyze <n> devices / <m> samples";
  - TASK to the correlator → "correlate <k> findings (round r)";
  - TASK to the reporter → "assemble unified report";
  - normalizer FINDING → "normalized <format>, data quality <pct>%".

  Never show "data quality 1" or bare key lists like "findings, telemetry, limitations, round".
- **F8 — trigger window:** format as `HH:MM:SS – HH:MM:SS UTC`. When first equals last, show "single sample @ HH:MM:SS UTC". Show "SAMPLED OVER n SECONDS" only when n > 0; otherwise "SINGLE SAMPLE", or "NO SAMPLES" when there are none.
- **F9 — "Flagged: 1 devices"** → pluralise correctly, everywhere.

## Layout vs reference (docs/ui-reference)
- **F10 — evidence tiles:** 3 tiles sit narrow with empty space on the right; the reference fills the row. Use `grid-template-columns: repeat(auto-fit, minmax(150px, 1fr))`. Values stay on one line (`white-space: nowrap`), with the unit in smaller muted text after the number.
- **F11 — root-cause callout:** move the long "Limitations: …" paragraph out of the callout into a collapsible "Limitations (n)" row under it. The callout keeps the label, the quote, the affected-devices line and the meta row, as in the reference.
- **F12 — affected-device chip:** show the worst evidence in plain words ("Queue drops 275 · critical") in red/amber like the reference chip, not `queue_drops = 275 count`.
- **F13 — telemetry table:** show evidence metrics and converted or aliased fields first, max 6 rows, then a "+n more" toggle. For canonical input where raw equals normalized, show the raw path (`devices[0].queue_drops`) rather than repeating the same text.
- **F14 — alternatives:** when the report has no alternatives, show the top 2 rejected hypotheses from details as **Ruled Out** cards with their real scores, so the section matches the reference's 2-card layout. If there are none at all, show one muted line.
- **F15 — mobile:** the top tabs wrap ("Telemetry Stream" breaks onto 2 lines). Make the tab bar a single horizontally scrollable row (`overflow-x: auto; white-space: nowrap`). Below 760px, move "Execute Quarantine" into the overflow menu so it isn't the first thing users see.

## Repo hygiene
- **F16** — add `.claude/` to `.gitignore`. Remove the duplicated T19 row and the duplicated S8 session-log line in `docs/TASKS.md`.

## Verify (Group 6 acceptance)
- Run congestion, dns_failure, org_hardware_failure, malformed_json and insufficient_data, plus `fail infrastructure` followed by `run congestion`. Every state reads correctly with no raw enum names or "—" placeholders where data exists. Also run Apply Simulated Remediation on congestion and check that the banner shows before → after with the verified result.
- No horizontal page scroll at 375px or 1280px. `pytest -q` passes. Compare the 1280px screenshot with `docs/ui-reference/*.png`.
