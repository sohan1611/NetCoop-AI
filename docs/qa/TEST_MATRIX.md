# NetCoop AI - Systematic QA Test Matrix

**Status:** All cases below are **NOT EXECUTED against the deployment** in this review. This is an implementation-driven acceptance checklist, not a pass/fail certificate. Use synthetic data and local test environment for stress/security probes.

Pass only when actual outcome, timestamp, browser/version, run ID and evidence screenshot/log are recorded.


## Core scenario correctness (16 checks)

| ID | Input / action | Pass criterion | Review status |
|---|---|---|---|
| S01 | normal | Report diagnosed/normal; no false alert; non-critical badge | Not executed |
| S02 | congestion | Root cause congestion; queue/util/latency evidence attached to same device/time | Not executed |
| S03 | weak_wifi | Weak RSSI plus retransmission evidence; correct AP affected | Not executed |
| S04 | interface_fault | Interface errors and loss corroborate; exact bad interface if available | Not executed |
| S05 | device_overload | CPU and memory evidence correctly aligned with affected device | Not executed |
| S06 | dns_failure | DNS-specific failure, service/server evidence, not DHCP or generic link outage | Not executed |
| S07 | dhcp_failure | DHCP-specific failure, lease service evidence, not DNS | Not executed |
| S08 | interop_renamed_fields_units | All known aliases and units converted to same canonical scores | Not executed |
| S09 | schema_v0_legacy | Legacy schema version conversion documented and diagnosis preserved | Not executed |
| S10 | missing_sections | Expected incomplete-data handling with appropriate limitations | Not executed |
| S11 | malformed_json | Clear 400/diagnostic error, no 500, no phantom successful diagnosis | Not executed |
| S12 | conflicting_evidence | Uncertainty explicit; show competing hypotheses, no unjustified exact cause | Not executed |
| S13 | insufficient_data | Status insufficient_evidence with Unknown health, not Degraded/P3 | Not executed |
| S14 | org_normal | Organizer-generated healthy sample accepted without false abnormality | Not executed |
| S15 | org_latency_spike | Latency_degradation distinguishable from link congestion | Not executed |
| S16 | org_hardware_failure | Hardware failure overruled correctly and higher raw competitors disclosed | Not executed |

## Telemetry and interoperability (24 checks)

| ID | Input / action | Pass criterion | Review status |
|---|---|---|---|
| N01 | 01_disconnected_signals | Do not join unrelated devices to claim single congestion RCA | Not executed |
| N02 | 02_impossible_values | Invalid percentages/counters excluded from diagnosis and itemized | Not executed |
| N03 | 03_all_samples_six_years_old | Old batch rejected as current evidence | Not executed |
| N04 | 04_same_timestamp_complementary_metrics | Merge non-conflicting sample fields, retain both observations | Not executed |
| N05 | 05_conflicting_alias_canonical_first | Conflicting units result in conflict/abstention, not arbitrary last-win | Not executed |
| N06 | 05_conflicting_alias_alias_first | Reverse key order does not change diagnosis vs N05 | Not executed |
| N07 | 06_warn_only_in_healthy_fleet | A warning device cannot be erased by healthy averages | Not executed |
| N08 | 07_future_dated_telemetry | Future timestamp flagged, ignored as live evidence | Not executed |
| N09 | 08_last_five_samples_missing_from_chart | Newest 5 critical records remain in chart/projections | Not executed |
| N10 | 09_topology_dangling_link | Dangling edges reported; no implicit phantom device | Not executed |
| N11 | 10_version_field_self_schema | Canonical legacy alias version recognized or generator corrected | Not executed |
| N12 | 11_signals_24h_apart | Do not fuse unrelated times into one simultaneous fault | Not executed |
| N13 | 12_unsupported_schema_control | Unknown schema version clearly rejected | Not executed |
| N14 | timestamp Z vs +00:00 | Equivalent instants de-duplicate/merge consistently | Not executed |
| N15 | timestamps missing or invalid | Explicit provenance/uncertainty instead of invented time | Not executed |
| N16 | numeric strings with percent suffix | No incorrect x100 conversion or unit ambiguity | Not executed |
| N17 | RSSI inverted threshold | -80 dBm worse than -55 dBm and not inverted twice | Not executed |
| N18 | null optional RSSI on wired device | No spurious corruption penalty for N/A radio fields | Not executed |
| N19 | multiple devices sharing same metric name | Only network-connected/time-correlated evidence combined | Not executed |
| N20 | device duplicate with conflicting statuses | Keep provenance, flag disagreement, do not silently last-win | Not executed |
| N21 | only services, no topology or samples | Service diagnosis possible, other agents honestly abstain | Not executed |
| N22 | only topology, no device status | No false precise hardware root cause | Not executed |
| N23 | zero records / [] / {} / null | Clear no-evidence status without 500 | Not executed |
| N24 | very long device ID / unicode characters | No backend crash or dangerous HTML injection; strings display literally | Not executed |

## Agent, confidence and reporting (18 checks)

| ID | Input / action | Pass criterion | Review status |
|---|---|---|---|
| A01 | six roles agent orchestration | Normalizer, 3 analysts, correlator, reporter task/finding IDs exposed | Not executed |
| A02 | interoperable envelopes | Validate all msg IDs, sender/recipient, types, schema version and payload | Not executed |
| A03 | unknown receiver | ERROR envelope with recoverable code, not dropped message | Not executed |
| A04 | low confidence | No exact cause when score below gate or signature coverage inadequate | Not executed |
| A05 | agent disagreement | Show all leading hypotheses and reasons for chosen override | Not executed |
| A06 | query/response rounds | At most 2 rounds, reason and target agent visible | Not executed |
| A07 | agent infrastructure failure | Continue degraded, mark limitation and missing evidence | Not executed |
| A08 | agent normalizer failure | Reject invalid input clearly; no phantom downstream agent completion | Not executed |
| A09 | agent reporter failure | Produce fallback JSON with honest limitations | Not executed |
| A10 | correlator failure | Fallback insufficient_evidence report, no invented certainty | Not executed |
| A11 | no topology connectivity abstain | Abstention excluded from denominator and distinct from failure | Not executed |
| A12 | structured JSON schema | All required fields and numeric confidence 0..1 | Not executed |
| A13 | alternative causes scored | Explain higher scoring alternatives if a domain rule overrides them | Not executed |
| A14 | evidence provenance | Every decisive metric carries actual device/unit/time/baseline when known | Not executed |
| A15 | run JSON and Markdown consistency | Cause, confidence, alternatives, limitations agree | Not executed |
| A16 | normal status but samples warning | No "Healthy" if actual warnings are present unless justified | Not executed |
| A17 | confidence monotonicity | Removing reliable evidence must not increase confidence without reason | Not executed |
| A18 | data corruption monotonicity | More bad data must not produce greater unwarranted certainty | Not executed |

## UI, interactions, exports (35 checks)

| ID | Input / action | Pass criterion | Review status |
|---|---|---|---|
| U01 | initial state | No empty placeholders or falsely active incidents, run controls coherent | Not executed |
| U02 | Trigger Swarm Diagnostic Run | Uses chosen scenario, disables duplicate submits, shows progress and result | Not executed |
| U03 | Spawn Agent Run modal | Keyboard escape/focus, scenario list, file input, cancel/confirm | Not executed |
| U04 | Upload JSON 5 MB | Valid upload accepted, over limit explicitly rejected | Not executed |
| U05 | invalid JSON file | Readable validation error and no optimistic diagnosis | Not executed |
| U06 | scenario changing with upload selected | Correctly clears file or makes source choice explicit | Not executed |
| U07 | congestion after normal | All cards and message logs reset, no stale values remain | Not executed |
| U08 | Acknowledge All then new congestion | New run_id remains unacknowledged | Not executed |
| U09 | normal critical badge | Healthy high confidence does NOT increment CRIT | Not executed |
| U10 | insufficient evidence banner | Unknown/No Evidence not Degraded | Not executed |
| U11 | agent votes display | k/n consensus not confused with numeric confidence | Not executed |
| U12 | topology affected root coloring | Faulty root red or both status markers, not amber-only | Not executed |
| U13 | topology click device | Correct status/worst metrics/no phantom device | Not executed |
| U14 | Incidents history and recent-first sort | All recorded completed/error runs visible, no disappearance | Not executed |
| U15 | Agent Pipeline | Each agent role, abstention, retry, reporter completion visible | Not executed |
| U16 | Telemetry Stream on 20k samples | Virtualized/paginated, no long browser freeze | Not executed |
| U17 | Evidence search | Evidence card can be searched or search scope is explicit | Not executed |
| U18 | Bus search | No contradictory results between bus/telemetry/incidents views | Not executed |
| U19 | CLI help and run command | Valid scenarios run, invalid command shows proper usage | Not executed |
| U20 | CLI fail infrastructure; run twice | Failure injection is one-run only, or persistent state labeled | Not executed |
| U21 | CLI remediate | Simulated-only, with confirmation, before/after and verification | Not executed |
| U22 | Copy Runbook Step | Copied exact action; graceful clipboard-permission failure | Not executed |
| U23 | JSON export | Download matches API report and preserves all structured fields | Not executed |
| U24 | Markdown export | Download contains cause/evidence/uncertainty/actions accurately | Not executed |
| U25 | Print PDF report | No blank/offscreen cards, all evidence and limitations print | Not executed |
| U26 | Deep link ?run=... | Correct historical report loads; unknown ID shows clear 404 | Not executed |
| U27 | History when run expired | Clear expired report message, not endless spinner | Not executed |
| U28 | SSE network drop | Automatic reconnect or status fallback, not permanent wrong stopped state | Not executed |
| U29 | Mobile 375px | No body horizontal scroll, navigation/menus still usable | Not executed |
| U30 | Tablet 768px and desktop 1280px | No cut-off status badges, tables, inputs, charts | Not executed |
| U31 | Dark/light print and contrast | Readable color contrast; high priority warnings clearly distinguished | Not executed |
| U32 | Keyboard only | Tabs, dialogs, buttons, topology, export accessible with focus | Not executed |
| U33 | Screen reader | Live progress polite announcements, roles and labels accurate | Not executed |
| U34 | Slow backend cold start | Correct waking banner, no repeated duplicate launches | Not executed |
| U35 | Unknown fields and alias mapping | UI raw->normalized table reflects actual conversion/conflicts | Not executed |

## API, abuse protection, consistency (24 checks)

| ID | Input / action | Pass criterion | Review status |
|---|---|---|---|
| H01 | GET /api/health | Returns documented limits and version, safe errors | Not executed |
| H02 | GET /api/scenarios | Lists exactly 16 supported labeled cases | Not executed |
| H03 | GET /api/scenarios/labels | Cannot fetch hidden labeling file as scenario | Not executed |
| H04 | POST /api/run valid scenario | Returns run_id then safe report without backend exception | Not executed |
| H05 | POST /api/run both source fields | Reject ambiguous both-specified request | Not executed |
| H06 | POST /api/run no source | Descriptive 400 | Not executed |
| H07 | invalid run IDs | No directory traversal, 400/404, no sensitive errors | Not executed |
| H08 | SSE nonexistent run ID | 404, never successful done without run | Not executed |
| H09 | SSE valid completed run | Replay exactly once, completion event ends stream | Not executed |
| H10 | report/details/topology missing run | 404 rather than 500 or silent empty result | Not executed |
| H11 | per-IP request rate limits | 10/min for /api/run and appropriate upload quotas | Not executed |
| H12 | concurrent run gate | 3rd simultaneous run 429; counter recovers on all errors | Not executed |
| H13 | upload without running | Stale files are cleaned and bounded | Not executed |
| H14 | large topology under 5MB | Caps on nodes/links/services and time/memory | Not executed |
| H15 | unauthorized run listing | Session B cannot see Session A private runs unless explicitly shared | Not executed |
| H16 | unauthorized stream fetch | Session B cannot download Session A raw synthetic upload | Not executed |
| H17 | remediation ownership | Session B cannot remediate Session A run | Not executed |
| H18 | report data redaction | No tokens/secrets/PII in publicly shared logs | Not executed |
| H19 | HTML XSS payload in device_id | No executable script via telemetry/run IDs or messages | Not executed |
| H20 | security headers | CSP, frame denial, nosniff and appropriate cross-origin controls | Not executed |
| H21 | run TTL cleanup | Expired runs report as expired and do not leak through history | Not executed |
| H22 | unexpected worker crash | Failure remains visible as incident status, limits and error safe | Not executed |
| H23 | backend restarts | Graceful documented state loss/restart status | Not executed |
| H24 | all endpoints HTTP status codes | Use 400/404/409/413/422/429 appropriately, not blanket 200/500 | Not executed |

**Total: 117 independent check items** across 5 groups.

