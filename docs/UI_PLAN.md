# UI Plan — "Autonomous NOC Swarm" console (T19)

The layout must be a near pixel match for `docs/ui-reference/ref-1-top.png` and `ref-2-bottom.png` (reference viewport 1280 × 2446).
**Hard rule:** every number, name and message on screen comes from a real run (API data). No hard-coded fake values such as "142 campus nodes" or "94%". If data is missing, show `—` or an empty-state line. Simulated actions are labelled "Simulated" (the PS forbids touching real infrastructure, and the rule book forbids fabricated results).

Stack stays the same: plain `api/static/{index.html,style.css,app.js}`, no build step (Vercel serves `api/static`). Fonts come from Google Fonts. Charts are hand-written SVG. Icons are inline SVG (Lucide paths, ISC licence, acknowledged in the README). Cytoscape (already SRI-pinned) is used only for the Topology view.

## 1. Design tokens (`:root` in style.css)
| Token | Value | Use |
|---|---|---|
| `--bg` | `#0a0f1a` | page; add a dot grid: `radial-gradient(#1b2436 1px, transparent 1px) 0 0/22px 22px` |
| `--panel` | `#0f1726` | cards |
| `--panel-2` | `#131d2e` | inner tiles, table rows |
| `--border` | `#1e2a3d` | 1px card borders, radius 6px |
| `--text` | `#e6edf7` | primary text |
| `--muted` | `#8a97ab` | secondary text |
| `--dim` | `#5b6880` | mono labels (uppercase, 10–11px, letter-spacing .08em) |
| `--cyan` | `#22d3ee` | accent, primary buttons (text `#06222a`), links, consensus |
| `--green` | `#22c55e` | complete / healthy / ruled out |
| `--amber` | `#f59e0b` | flagged / warning / partial contributor |
| `--red` | `#ef4444` | alert / critical; danger button bg `#b91c1c` |
| fonts | `Inter` 400/500/600/700 for UI, `JetBrains Mono` 400/500 for labels, code, the bus and the console |

Sizes: hero title 30px/700, section titles 15px/600, card titles 14px/600, metric values 26–30px/700, body 12.5px.
Pills: 10px mono, 2px 8px padding, 1px border in the status colour at 40% alpha, background at 12% alpha.
Cards: `border:1px solid var(--border); background:var(--panel); padding:16px`. The active nav item gets a 2px cyan left bar and a `--panel-2` background.

## 2. Layout grid (desktop ≥ 1200px)
```
┌ top bar 56px, full width ───────────────────────────────────────────────┐
├ sidebar 220px ┬ main (fluid, max 1100) ─────────────┬ right rail 300px ───┤
│               │ Hero card                           │ (starts at Unified  │
│               │ Pipeline card (agents)              │  Report row)        │
│               │ Unified Diagnostic Report card ─────│ Latency trend       │
│               │   root cause, affected, evidence,   │ 2 stat tiles        │
│               │   telemetry table, hypotheses,      │ Agent Bus (sticky,  │
│               │   corrective actions                │  fills height)      │
└───────────────┴─────────────────────────────────────┴─────────────────────┘
```
Hero and Pipeline span main + right rail. From the Report row down: a two-column grid `minmax(0,1fr) 300px`, gap 16px.
Below 1200px the right rail moves under the report. Below 760px the sidebar becomes a top drawer (hamburger) and everything stacks in one column, with no horizontal page scroll; tables scroll inside their own container.

## 3. Components, top to bottom (match the reference; data source in brackets)
**Top bar.** Logo icon plus two lines: "Autonomous Agent Diagnostics" / mono subtitle `Campus Core Grid • <incident_id or 'idle'>`. Nav tabs: Console · Topology · Incidents · Agent Pipeline · Telemetry Stream (the active one gets a cyan underline; they switch views, §5). Search input placeholder `Grep flow, IP, MAC...` filters the bus, the evidence and the telemetry table client-side. Then **Acknowledge All** (outline; marks the current run's incident acknowledged in localStorage and clears the CRIT badge), **Execute Quarantine** (red; opens a confirm modal → `POST /api/runs/{id}/remediate`; the modal and result say "Simulated remediation"), three icon buttons (notifications, terminal, settings) and the avatar "NC".

**Sidebar.** Card "NetCoop AI / Swarm Controller 01". Full-width cyan **Spawn Agent Run** button → opens the Run modal (§4). Header "OPERATIONAL DOMAINS"; items Live Topology, Telemetry Agents, Correlated Incidents (red badge `<n> CRIT` = runs with status diagnosed and confidence ≥ 0.75 that aren't acknowledged), Device Mesh, Automated Playbooks, CLI Terminal. Bottom: Telemetry Feeds, System Settings, and a mono footer `HOST: <api host>  UP: <uptime from /api/health>`.

**Hero card.** Two chips: `NETCOOP AI ROOT-CAUSE ENGINE` (cyan) and `// ORCHESTRATOR CLUSTER` (dim). Title "NetCoop AI // Autonomous NOC Swarm v<version>" (version from `/api/health`). One-line description. Buttons: **Trigger Swarm Diagnostic Run** (cyan; runs the scenario selected in the Run modal, default `congestion`) and **Export Root-Cause Report (JSON/PDF)** (menu with JSON download from `/report`, Markdown from `/report.md`, and PDF via `window.print()` with a print stylesheet).
Four stat tiles:
- **CAMPUS MESH HEALTH**: Healthy / Degraded / Critical from the report status and confidence; coloured dot.
- **AGENT STATUS**: "Swarm Status: <n> Active Diagnostic Agents", where n = agents that produced a finding.
- **COORDINATION CYCLE**: "Last Coordinated Sweep: <relative time since run end>", plus "<rounds> query rounds".
- **CORRELATED ANOMALY**: "Active Incident: <incident_id>" with a severity badge: P1 CRIT for high confidence and not normal, P2 for medium, P3 for low or insufficient.

**Pipeline card.** Title "Autonomous Multi-Agent Coordination Pipeline". Right side: `PIPELINE CONSENSUS: <SYNCHRONIZED|DIVERGENT> (<k>/<n> AGENTS ENGAGED)`, from the agreement data. Agent cards (grid `repeat(auto-fit,minmax(190px,1fr))`) for normalizer, performance, infrastructure, connectivity and correlator, in that order. Each card shows:
- a `// AGENT-0x:` mono label;
- a status pill (Pending grey / Running cyan pulse / Complete green / Flagged amber when the top hypothesis isn't normal / Alert red when there's critical evidence / Failed red when the agent is unavailable / Abstained grey / Synthesizing cyan for the correlator during query rounds);
- the category label (INGESTION, PERFORMANCE, INFRASTRUCTURE, TOPOLOGY, RCA);
- the agent name and a 2–3 line summary built from its finding notes and top hypothesis;
- two mono footer stats from the agent summary (e.g. "Ingested: <n> records" + "<k> fields mapped").

Statuses update live from SSE.

**Unified Diagnostic Report card** (main column):
- Header: icon, "Unified Diagnostic Report", a chip `CORRELATION HASH: #<run_id>`, and a cyan chip `Format Normalization: <k> conflicting formats (<list>)` taken from the normalizer formats and mappings.
- Root-cause callout: 3px cyan left border, label "SYNTHESIZED PROBABLE ROOT CAUSE" with the cause word highlighted, the quoted `suspected_root_cause` + affected devices sentence in 17px/600. Meta row: "Trigger Window: <first>–<last sample time>" · "Multi-Agent Consensus: <confidence %> <category> Confidence". If the status is `insufficient_evidence` or `error`, the callout turns amber or red and shows the limitations instead.
- **Affected Devices & Campus Network Segments**: up to 3 cards (role label, device id, location/type, a chip with the worst evidence for that device: red for critical, amber for warning). If a 4th device exists, show "+n more".
- **Supporting Evidence & Telemetry Findings**: header right `SAMPLED OVER <window seconds> SECONDS`. Up to 5 metric tiles (label, value + unit coloured by severity, "Baseline: <baseline>" or "Threshold: <threshold>"). Then a table with columns Telemetry Feed (source format, cyan), Source Device, Raw Payload Fragment (mono, e.g. `metrics.cpu_utilization_pct = 99.9`) and Normalized Metric (coloured `cpu_pct = 99.9 %`), from the normalizer mappings for the affected devices.
- **Hypothesis Confidence & Alternative Explanations Evaluated**: primary box "Primary Hypothesis: <cause> on <device> — <conf %> (<category>)" with a cyan progress bar and a one-line rationale; then a card per alternative (grid of 2) with name, "<p>% Probability", a thin bar, and a status label: Ruled Out (green) if p < 0.2, otherwise Partial Contributor (amber), plus its rationale.
- **Recommended Corrective Actions**: right label `VALIDATED BY PLAYBOOK`. Numbered rows (number box, title, description). The first row's button is cyan **Apply Simulated Remediation**, which opens the confirm modal → remediate → shows a "before → after, verified ✓/✗" banner. Other rows get outline buttons that copy the action to the clipboard, labelled "Copy Runbook Step".

**Right rail:**
- **CAMPUS LATENCY TREND**: header right `+<x>% Anomaly` (red) = (latest − baseline)/baseline. A big value "<latest> ms". An SVG area/line chart (red line, faint grid, x labels = first/last timestamp) from the latency series of the primary affected device. With fewer than 2 points, show the single value plus "single sample".
- **Two tiles**: PACKET LOSS RATE (worst `packet_loss_pct` + device, red when critical) and SWARM CONSENSUS (confidence % + "<k>/<n> Agents in Accord").
- **Agent Inter-Communication Bus**: a green live dot. Each message shows a mono header `[<Sender> → <Recipient>]` and an HH:MM:SS.mmm timestamp, then a quoted summary in a colour by type (TASK dim, FINDING cyan, QUERY amber, RESPONSE cyan, ERROR red) with a 2px left border in the same colour. Auto-scrolls. The bottom input has the prompt `ai-orchestrator >` and placeholder `query swarm or issue cl…` plus a send icon. It's a local command parser (no LLM): `help`, `run <scenario>`, `fail <agent>`, `evidence`, `alts`, `remediate`, `export json`. Output appears in the bus as `[You → Orchestrator]` messages.

## 4. Run modal (Spawn Agent Run)
Scenario select (from `/api/scenarios`, showing the expected label), or upload a JSON file (`/api/upload`, max 5 MB). Checkboxes for fault-injection agents. An "LLM assist" toggle is shown disabled with the note "server runs rules-only". Start → `POST /api/run` → close the modal, reset the view, open SSE.

## 5. Views (top tabs and sidebar items switch `data-view` on `<main>`)
- **Console**: everything above (default).
- **Topology**: full-width Cytoscape graph; affected nodes red, upstream root amber; node click shows device metrics.
- **Incidents**: a table from `GET /api/runs` (time, incident, cause, confidence, status, acknowledged); clicking a row loads that run.
- **Agent Pipeline**: pipeline card plus a per-agent message list.
- **Telemetry Stream**: the full bus plus the normalized samples table.
- **Device Mesh**: the device table from details.
- **Automated Playbooks**: `GET /api/playbook`.
- **CLI Terminal**: the console input enlarged.
- **Telemetry Feeds / System Settings**: simple info panels (API base, version, limits).

## 6. States
- **Idle** (no run yet): skeleton shimmer cards, with the hero CTA highlighted.
- **Running**: pulsing pills, bus streaming, report area showing "Agents correlating…".
- **Error / insufficient**: shown as described above.
- **API cold start** (Render free tier, up to 50 s): a banner "Waking diagnostic backend…" with retry.
- All dynamic text is set via `textContent` (no `innerHTML` with data). CSP must be updated for Google Fonts: `style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com`, in both the server headers and `vercel.json` `headers`.

## 7. Acceptance (T19)
- Side-by-side with `docs/ui-reference/*.png` at 1280px: same sections, order, colours, typography scale and spacing (±4px).
- Running `congestion`, `dns_failure`, `org_hardware_failure`, `malformed_json`, `insufficient_data` and `--fail-agent infrastructure` from the modal fills every panel from API data with no console errors.
- The page works at 375px width (no horizontal scroll) and print-to-PDF gives a clean report.
- `pytest -q` still passes. Add `tests/test_api.py` cases for the new endpoints (T18).
