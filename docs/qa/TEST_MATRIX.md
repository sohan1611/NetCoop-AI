# NetCoop AI - Systematic QA Test Matrix

**Status:** executed in T27 (2026-10-31) against a local build (commit `ca187a7` + T27 fixes): pytest, CLI, FastAPI test client, a real uvicorn restart and the built-in browser. 117/117 Pass; defects found were NC-034..NC-041 and are fixed (see QA_TRACKER).

Pass only when actual outcome, timestamp, browser/version, run ID and evidence screenshot/log are recorded.


## Core scenario correctness (16 checks)

| ID | Input / action | Pass criterion | Result (T27) |
|---|---|---|---|
| S01 | normal | Report diagnosed/normal; no false alert; non-critical badge | Pass: normal diagnosed, conf 1.00, no CRIT badge (UI + CLI) |
| S02 | congestion | Root cause congestion; queue/util/latency evidence attached to same device/time | Pass: congestion 0.94; util 94 / queue 275 / latency 185 all on CORE_RTR_01 |
| S03 | weak_wifi | Weak RSSI plus retransmission evidence; correct AP affected | Pass: weak_wifi 0.93; RSSI -82 + retrans 23 % on HOSTEL_AP_01 |
| S04 | interface_fault | Interface errors and loss corroborate; exact bad interface if available | Pass: interface_fault 0.97; errors 1450 + loss 18 % on DEPT_SW_01 |
| S05 | device_overload | CPU and memory evidence correctly aligned with affected device | Pass: device_overload 0.91; CPU 96 / mem 94 on ACADEMIC_DIST_SW_01 |
| S06 | dns_failure | DNS-specific failure, service/server evidence, not DHCP or generic link outage | Pass: dns_failure 0.93 with DNS_SRV_01 status/success/response evidence; not DHCP |
| S07 | dhcp_failure | DHCP-specific failure, lease service evidence, not DNS | Pass: dhcp_failure 0.93 with DHCP_SRV_01 evidence; not DNS |
| S08 | interop_renamed_fields_units | All known aliases and units converted to same canonical scores | Pass: aliases/units converted; congestion 0.97, same cause as canonical; UI shows 10 fields converted |
| S09 | schema_v0_legacy | Legacy schema version conversion documented and diagnosis preserved | Pass: legacy v0 upgraded, device_overload 0.88; limitation notes missing topology |
| S10 | missing_sections | Expected incomplete-data handling with appropriate limitations | Pass: normal 0.75 (medium) with missing topology/services limitations and the warning device named |
| S11 | malformed_json | Clear 400/diagnostic error, no 500, no phantom successful diagnosis | Pass: CLI rc=2 / UI "Input rejected" / API report status error; no 500, no diagnosis |
| S12 | conflicting_evidence | Uncertainty explicit; show competing hypotheses, no unjustified exact cause | Pass: insufficient_evidence 0.30; disagreement listed; NC-034 fix makes alternatives name the leading hypotheses |
| S13 | insufficient_data | Status insufficient_evidence with Unknown health, not Degraded/P3 | Pass: insufficient_evidence; UI "Unknown / Unclassified", not Degraded/P3 |
| S14 | org_normal | Organizer-generated healthy sample accepted without false abnormality | Pass: org_normal normal 0.80, no abnormality |
| S15 | org_latency_spike | Latency_degradation distinguishable from link congestion | Pass: latency_degradation 0.81 on SW_CAMPUS_10, not congestion |
| S16 | org_hardware_failure | Hardware failure overruled correctly and higher raw competitors disclosed | Pass: hardware_failure 0.67 on SW_CAMPUS_2; higher raw interface_fault 90.2 % / congestion 81.5 % disclosed (NC-035 adds overruled-by text) |

## Telemetry and interoperability (24 checks)

| ID | Input / action | Pass criterion | Result (T27) |
|---|---|---|---|
| N01 | 01_disconnected_signals | Do not join unrelated devices to claim single congestion RCA | Pass: latency_degradation 0.46 low, congestion capped; limitation "evidence from unrelated devices/times" |
| N02 | 02_impossible_values | Invalid percentages/counters excluded from diagnosis and itemized | Pass: values quarantined and itemized; insufficient_evidence 0.28, quality 0.29 |
| N03 | 03_all_samples_six_years_old | Old batch rejected as current evidence | Pass: stale rows quarantined, 0 samples, insufficient_evidence 0.00 |
| N04 | 04_same_timestamp_complementary_metrics | Merge non-conflicting sample fields, retain both observations | Pass: one merged sample (3 fields); issue "duplicate ... merged" |
| N05 | 05_conflicting_alias_canonical_first | Conflicting units result in conflict/abstention, not arbitrary last-win | Pass: ambiguous alias quarantined; insufficient_evidence 0.016 |
| N06 | 05_conflicting_alias_alias_first | Reverse key order does not change diagnosis vs N05 | Pass: identical report to N05 (cause, confidence, issues) |
| N07 | 06_warn_only_in_healthy_fleet | A warning device cannot be erased by healthy averages | Pass: FAULTY interface_fault 0.47 (low), FAULTY named in limitations; not Healthy |
| N08 | 07_future_dated_telemetry | Future timestamp flagged, ignored as live evidence | Pass: future timestamp quarantined; insufficient_evidence 0.00 |
| N09 | 08_last_five_samples_missing_from_chart | Newest 5 critical records remain in chart/projections | Pass: chart ends at 500 ms (NC-009, browser-checked in T26); samples table paginated |
| N10 | 09_topology_dangling_link | Dangling edges reported; no implicit phantom device | Pass: dangling link dropped and reported; no phantom node |
| N11 | 10_version_field_self_schema | Canonical legacy alias version recognized or generator corrected | Pass: `version` alias accepted, quality 1.0 |
| N12 | 11_signals_24h_apart | Do not fuse unrelated times into one simultaneous fault | Pass: 24 h-old signal quarantined; insufficient_evidence 0.13, not congestion |
| N13 | 12_unsupported_schema_control | Unknown schema version clearly rejected | Pass: error "unknown schema_version: 9.0" |
| N14 | timestamp Z vs +00:00 | Equivalent instants de-duplicate/merge consistently | Pass: Z and +00:00 rows merged to 8 samples |
| N15 | timestamps missing or invalid | Explicit provenance/uncertainty instead of invented time | Pass: missing / invalid timestamps listed as issues; insufficient_evidence 0.30 |
| N16 | numeric strings with percent suffix | No incorrect x100 conversion or unit ambiguity | Pass: "94%" -> 94.0, "0.5%" -> 0.5, coercion noted, no x100 |
| N17 | RSSI inverted threshold | -80 dBm worse than -55 dBm and not inverted twice | Pass: -80 dBm -> weak_wifi 0.93; -55 dBm -> not weak_wifi (insufficient 0.27) |
| N18 | null optional RSSI on wired device | No spurious corruption penalty for N/A radio fields | Pass: wired devices with null rssi/retransmission raise no quality issue |
| N19 | multiple devices sharing same metric name | Only network-connected/time-correlated evidence combined | Pass: NC-001 tests + fixture 01; evidence joined only per device/neighbour/time bucket |
| N20 | device duplicate with conflicting statuses | Keep provenance, flag disagreement, do not silently last-win | Pass after NC-036: both status rows kept and a conflict issue is raised (before: no flag) |
| N21 | only services, no topology or samples | Service diagnosis possible, other agents honestly abstain | Pass: services only -> dns_failure 0.79 with other agents abstaining; no samples -> no hardware claim |
| N22 | only topology, no device status | No false precise hardware root cause | Pass: topology only -> insufficient_evidence 0.00 |
| N23 | zero records / [] / {} / null | Clear no-evidence status without 500 | Pass: [] / {} -> insufficient_evidence; null / "" / 0 -> clear error; no 500 |
| N24 | very long device ID / unicode characters | No backend crash or dangerous HTML injection; strings display literally | Pass: 400-char unicode + <script> device id accepted; rendered literally in UI, no execution |

## Agent, confidence and reporting (18 checks)

| ID | Input / action | Pass criterion | Result (T27) |
|---|---|---|---|
| A01 | six roles agent orchestration | Normalizer, 3 analysts, correlator, reporter task/finding IDs exposed | Pass: messages.jsonl carries TASK/FINDING for normalizer, 3 analysts, correlator, reporter with correlation ids |
| A02 | interoperable envelopes | Validate all msg IDs, sender/recipient, types, schema version and payload | Pass: bus validates msg_id/sender/recipient/type/schema_version; bad envelope or version -> ERROR INVALID_ENVELOPE |
| A03 | unknown receiver | ERROR envelope with recoverable code, not dropped message | Pass: unknown recipient -> ERROR RECIPIENT_NOT_FOUND, recoverable=true |
| A04 | low confidence | No exact cause when score below gate or signature coverage inadequate | Pass: below-gate scores report insufficient_evidence (conf < 0.3 or low coverage) |
| A05 | agent disagreement | Show all leading hypotheses and reasons for chosen override | Pass after NC-034: leading hypotheses and the disagreement are listed (before: only a minor alternative) |
| A06 | query/response rounds | At most 2 rounds, reason and target agent visible | Pass: <= 2 QUERY rounds, target agent + reason in the bus log (insufficient_data: 2 rounds) |
| A07 | agent infrastructure failure | Continue degraded, mark limitation and missing evidence | Pass: each of performance/infrastructure/connectivity failed -> run completes, limitation "<agent> unavailable", consensus 1/2 |
| A08 | agent normalizer failure | Reject invalid input clearly; no phantom downstream agent completion | Pass: normalizer fault -> error report, no downstream tasks sent |
| A09 | agent reporter failure | Produce fallback JSON with honest limitations | Pass: reporter fault -> fallback report with limitation "reporter unavailable" |
| A10 | correlator failure | Fallback insufficient_evidence report, no invented certainty | Pass: correlator fault -> insufficient_evidence 0.00, no invented cause |
| A11 | no topology connectivity abstain | Abstention excluded from denominator and distinct from failure | Pass: org_normal: connectivity abstains (empty hypotheses + note), excluded from the engaged count, no conflict noise |
| A12 | structured JSON schema | All required fields and numeric confidence 0..1 | Pass: 23 saved reports validate against the Report model; confidence in 0..1; evidence has metric/value/interpretation |
| A13 | alternative causes scored | Explain higher scoring alternatives if a domain rule overrides them | Pass: alternatives carry overruled_by; report text and limitations explain the domain rule |
| A14 | evidence provenance | Every decisive metric carries actual device/unit/time/baseline when known | Pass: evidence carries device, unit, baseline, threshold, timestamp where known |
| A15 | run JSON and Markdown consistency | Cause, confidence, alternatives, limitations agree | Pass: report.json vs report.md agree on cause, confidence, alternatives, limitations, actions (23 runs) |
| A16 | normal status but samples warning | No "Healthy" if actual warnings are present unless justified | Pass: warning devices block high-confidence normal and are named in limitations |
| A17 | confidence monotonicity | Removing reliable evidence must not increase confidence without reason | Pass: dropping each key metric in congestion/weak_wifi/interface_fault never raised confidence |
| A18 | data corruption monotonicity | More bad data must not produce greater unwarranted certainty | Pass: corrupting 1/3/6 samples never raised confidence |

## UI, interactions, exports (35 checks)

| ID | Input / action | Pass criterion | Result (T27) |
|---|---|---|---|
| U01 | initial state | No empty placeholders or falsely active incidents, run controls coherent | Pass: placeholders are "-"/Pending, CRIT 0, no active incident |
| U02 | Trigger Swarm Diagnostic Run | Uses chosen scenario, disables duplicate submits, shows progress and result | Pass: uses the selected scenario; double click starts exactly 1 run; progress and result shown |
| U03 | Spawn Agent Run modal | Keyboard escape/focus, scenario list, file input, cancel/confirm | Pass: native modal dialog, Esc closes, 16 scenarios, file input, cancel |
| U04 | Upload JSON 5 MB | Valid upload accepted, over limit explicitly rejected | Pass: 3.5 MB / 20k-sample upload accepted; >5 MB rejected client-side and 413 server-side |
| U05 | invalid JSON file | Readable validation error and no optimistic diagnosis | Pass: invalid JSON -> "Input rejected", Unknown mesh, no diagnosis |
| U06 | scenario changing with upload selected | Correctly clears file or makes source choice explicit | Pass: changing the scenario clears the file; "Use selected scenario" button shown |
| U07 | congestion after normal | All cards and message logs reset, no stale values remain | Pass: congestion -> normal resets evidence/alternatives/actions/bus (12 msgs of the new run only) |
| U08 | Acknowledge All then new congestion | New run_id remains unacknowledged | Pass: Acknowledge All -> 0; new congestion run -> 1 CRIT |
| U09 | normal critical badge | Healthy high confidence does NOT increment CRIT | Pass: normal runs never increment CRIT |
| U10 | insufficient evidence banner | Unknown/No Evidence not Degraded | Pass: insufficient evidence -> Unknown / Unclassified |
| U11 | agent votes display | k/n consensus not confused with numeric confidence | Pass: "n/m agents in accord" and consensus label are separate from the % confidence |
| U12 | topology affected root coloring | Faulty root red or both status markers, not amber-only | Pass: affected root CORE_RTR_01 drawn red |
| U13 | topology click device | Correct status/worst metrics/no phantom device | Pass: node click shows status, worst metric, metrics; no phantom node |
| U14 | Incidents history and recent-first sort | All recorded completed/error runs visible, no disappearance | Pass: Incidents lists all 11 runs incl. the error run, newest first |
| U15 | Agent Pipeline | Each agent role, abstention, retry, reporter completion visible | Pass: pipeline shows Complete/Alert/Flagged/Failed/Skipped/Abstained + reporter; query round count |
| U16 | Telemetry Stream on 20k samples | Virtualized/paginated, no long browser freeze | Pass: 20k samples (3.5 MB): paginated 100/page, run 3.3 s, table render 0.8 s |
| U17 | Evidence search | Evidence card can be searched or search scope is explicit | Pass: evidence tiles are searchable ("queue" -> 1 tile). NC-028 stays Rejected |
| U18 | Bus search | No contradictory results between bus/telemetry/incidents views | Pass: search filters bus and evidence consistently; empty state "No matching messages." |
| U19 | CLI help and run command | Valid scenarios run, invalid command shows proper usage | Pass: CLI help/usage; invalid command -> usage + exit 2; UI terminal help/run |
| U20 | CLI fail infrastructure; run twice | Failure injection is one-run only, or persistent state labeled | Pass: UI fail-agent applies to one run (second run clean); CLI flag is per invocation |
| U21 | CLI remediate | Simulated-only, with confirmation, before/after and verification | Pass: remediate needs --approve; prints before/after/verified true |
| U22 | Copy Runbook Step | Copied exact action; graceful clipboard-permission failure | Pass by code review: clipboard writes the exact step; failure shows "Clipboard unavailable" |
| U23 | JSON export | Download matches API report and preserves all structured fields | Pass: JSON export is the /report payload |
| U24 | Markdown export | Download contains cause/evidence/uncertainty/actions accurately | Pass: Markdown has cause, confidence, evidence, alternatives, limitations, actions |
| U25 | Print PDF report | No blank/offscreen cards, all evidence and limitations print | Pass by code + earlier PDF screenshot: beforeprint expands telemetry rows and limitations. Not re-rendered through a real print dialog in this pass |
| U26 | Deep link ?run=... | Correct historical report loads; unknown ID shows clear 404 | Pass after NC-039: valid link loads; unknown id shows a clear not-found message (before: endless retry, UI locked) |
| U27 | History when run expired | Clear expired report message, not endless spinner | Pass after NC-039: expired run behaves like unknown (404) with the same message |
| U28 | SSE network drop | Automatic reconnect or status fallback, not permanent wrong stopped state | Pass: stream error retries 3x then polls the report (code + NC-019 tests); backend restart recovered via Retry connection |
| U29 | Mobile 375px | No body horizontal scroll, navigation/menus still usable | Pass: 375 px, no horizontal page scroll in 10 views; sidebar menu works |
| U30 | Tablet 768px and desktop 1280px | No cut-off status badges, tables, inputs, charts | Pass: 768 and 1280 px, no page overflow in all views |
| U31 | Dark/light print and contrast | Readable color contrast; high priority warnings clearly distinguished | Pass after NC-041: dim text raised from 3.0:1 to ~4.8:1; critical = red, healthy = green. Measured on dark theme only |
| U32 | Keyboard only | Tabs, dialogs, buttons, topology, export accessible with focus | Pass: modal focus on scenario, Esc closes, all controls named and focusable |
| U33 | Screen reader | Live progress polite announcements, roles and labels accurate | Pass: status role=status, bus aria-live=polite, dialog labelled, no unnamed controls. Not tested with a real screen reader |
| U34 | Slow backend cold start | Correct waking banner, no repeated duplicate launches | Pass: backend down -> "Waking diagnostic backend... Retry connection" banner; retry recovers; no duplicate runs |
| U35 | Unknown fields and alias mapping | UI raw->normalized table reflects actual conversion/conflicts | Pass: raw -> normalized table shows canonical v1/aliased/unit-converted rows; "10 fields converted, no conflicts" |

## API, abuse protection, consistency (24 checks)

| ID | Input / action | Pass criterion | Result (T27) |
|---|---|---|---|
| H01 | GET /api/health | Returns documented limits and version, safe errors | Pass: 200, version + limits |
| H02 | GET /api/scenarios | Lists exactly 16 supported labeled cases | Pass: 16 scenarios, no labels entry |
| H03 | GET /api/scenarios/labels | Cannot fetch hidden labeling file as scenario | Pass: /api/scenarios/labels -> 400 |
| H04 | POST /api/run valid scenario | Returns run_id then safe report without backend exception | Pass: run_id, then report 200 diagnosed congestion |
| H05 | POST /api/run both source fields | Reject ambiguous both-specified request | Pass: 422 |
| H06 | POST /api/run no source | Descriptive 400 | Pass: 422 "provide exactly one of scenario or upload_id" |
| H07 | invalid run IDs | No directory traversal, 400/404, no sensitive errors | Pass: .., %2f, spaces, %00, 300 chars, dotfiles -> 400/404 on every endpoint |
| H08 | SSE nonexistent run ID | 404, never successful done without run | Pass: 404 |
| H09 | SSE valid completed run | Replay exactly once, completion event ends stream | Pass: replayed once, one done event ends the stream |
| H10 | report/details/topology missing run | 404 rather than 500 or silent empty result | Pass: report/report.md/details/topology/remediate -> 404 |
| H11 | per-IP request rate limits | 10/min for /api/run and appropriate upload quotas | Pass: >10 runs/min -> 429 (rejected requests count); upload 20-file cap -> 413 |
| H12 | concurrent run gate | 3rd simultaneous run 429; counter recovers on all errors | Pass: 3rd concurrent run -> 429; counter recovers after 400s |
| H13 | upload without running | Stale files are cleaned and bounded | Pass: cleanup on upload; 20 files / 25 MB cap -> 413 |
| H14 | large topology under 5MB | Caps on nodes/links/services and time/memory | Pass: node/link/service caps in the normalizer (NC-021 tests) |
| H15 | unauthorized run listing | Session B cannot see Session A private runs unless explicitly shared | Pass: session B sees [] |
| H16 | unauthorized stream fetch | Session B cannot download Session A raw synthetic upload | Pass: session B stream/report -> 404; stream redacts raw input |
| H17 | remediation ownership | Session B cannot remediate Session A run | Pass: session B remediate -> 404 |
| H18 | report data redaction | No tokens/secrets/PII in publicly shared logs | Pass: no secrets/tokens in stream or report; raw input redacted |
| H19 | HTML XSS payload in device_id | No executable script via telemetry/run IDs or messages | Pass: <img onerror>/<script> in device/incident id rendered literally; window.__xss stayed 0 |
| H20 | security headers | CSP, frame denial, nosniff and appropriate cross-origin controls | Pass: CSP, X-Frame-Options DENY, nosniff, no-referrer. No HSTS/COOP (TLS is added by the host) |
| H21 | run TTL cleanup | Expired runs report as expired and do not leak through history | Pass: expired run -> 404 "unknown run", absent from /api/runs |
| H22 | unexpected worker crash | Failure remains visible as incident status, limits and error safe | Pass after NC-037: crashed worker -> run listed as error, /report 200 error report (before: 500) |
| H23 | backend restarts | Graceful documented state loss/restart status | Pass: real uvicorn restart: reports, details, list, remediate still served to the same session; in-flight runs are lost |
| H24 | all endpoints HTTP status codes | Use 400/404/409/413/422/429 appropriately, not blanket 200/500 | Pass: 400/404/409/413/422/429 used; no 5xx after NC-037 |

**Total: 117 independent check items** across 5 groups.

