# QA Tracker — adversarial audit (33 findings, 117-check matrix)

Source: external adversarial QA of commit `2d986d6` (findings in `docs/qa/findings.json`, report `docs/qa/NetCoop_AI_Adversarial_QA_Report.md` — both local only, git-ignored).
Fixtures: `tests/fixtures/qa/*.json` · runner: `PYTHONPATH=. python scripts/run_local_qa.py` · matrix: `docs/qa/TEST_MATRIX.md`.
Verified independently in S13 (Opus) by reading the cited code and running every fixture with `--no-llm`. Baseline: 198 tests pass, evaluate 16/16.

Status flow: **Confirmed → Fixed (task) → Tested (regression test name)** or **Rejected (reason)**.
Priority = ours after verification (audit priority in brackets when changed).

## Baseline fixture results (before fixes)

| Fixture | Got | Should be (acceptance) |
|---|---|---|
| 00 control_normal | normal 1.0 | normal, no CRIT |
| 01 disconnected_signals | **congestion 1.0** | not a confident single-device congestion; limitation "evidence from unrelated devices" |
| 02 impossible_values | **congestion 0.9** | impossible values quarantined + itemized; insufficient_evidence |
| 03 six_years_old | **congestion 1.0** | stale vs `collected_at` → quarantined; insufficient_evidence |
| 04 same_timestamp_complementary | 1 sample, latency only (other row dropped) | one merged sample with all 3 fields |
| 05 alias canonical-first / alias-first | **latency_degradation 0.62 / insufficient 0.12** | identical result both orders; latency marked ambiguous |
| 06 warn_only_in_healthy_fleet | **normal 0.7** | not Healthy; FAULTY device named as warning |
| 07 future_dated | **congestion 1.0** | future (> collected_at + 5 min) quarantined; insufficient_evidence |
| 08 last_five_missing_from_chart | chart = first 200 (healthy) | chart ends at 500 ms |
| 09 topology_dangling_link | phantom node in graph | dangling link dropped + issue reported |
| 10 version_field | quality 0.93 (`version` unknown) | `version` accepted as alias, quality 1.0 |
| 11 signals_24h_apart | **congestion 0.83** | not congestion (different moments) |
| 12 unsupported_schema | error | error (unchanged) |

## Findings

| ID | Pri | Verdict | Evidence (S13) | Task | Status |
|---|---|---|---|---|---|
| NC-001 | P0 | Confirmed | fixture 01 → congestion 1.0 from AP-A util + HOST-C latency; correlator maxes each metric fleet-wide | T24 | Confirmed |
| NC-002 | P0 | Confirmed | fixture 02 → congestion 0.9; `_validate_rows` flags but keeps values | T23 | Confirmed |
| NC-003 | P0 [P1] | Confirmed | fixtures 03/07/11 → congestion 0.83–1.0; cutoff = batch max − 1 h, stale rows kept, no future check | T23 (window) + T24 (time join) | Confirmed |
| NC-004 | P1 | Confirmed | fixture 04 → 1 sample; dedupe key drops 2nd row (normalizer.py:735-753) | T23 | Confirmed |
| NC-005 | P1 | Confirmed | fixture 05 results differ by key order | T23 | Confirmed |
| NC-006 | P1 | Confirmed | fixture 06 → normal 0.7 with FAULTY at 110 ms / 2.5 % loss | T24 | Confirmed |
| NC-007 | P1 | Confirmed | `/api/runs` + `/stream` + report/details have no ownership check; stream replays raw input | T25 | Confirmed |
| NC-008 | P2 [P1] | Confirmed (partial remedy) | upload never calls `admit()`, cleanup only on `/api/run`. Downgraded: 5 MB cap + TTL exist. Remedy "reject non-JSON early" **rejected**: malformed input must reach the normalizer (PS interop demo) | T25 | Confirmed |
| NC-009 | P1 | Confirmed | details.py:105 `points[:MAX_SERIES]` keeps oldest 200 | T26 | Confirmed |
| NC-010 | P1 | Confirmed | app.js:73 CRIT counts confidence ≥ 0.75 incl. normal | T26 | Confirmed |
| NC-011 | P2 [P1] | Confirmed | app.js:67 `incidentKey` prefers static incident_id. UX only (per-browser localStorage) | T26 | Confirmed |
| NC-012 | P2 | Confirmed | correlator.py:924-931 keeps only `confidence < winner`. Reverses T17 rule "no alternative ≥ chosen" — transparency wins; show with `overruled_by` | T24 | Confirmed |
| NC-013 | P3 [P2] | Partially confirmed | Claim that bundled scenarios use `version` is **false** (they use `schema_version`, normal.json:176). Missing `version` alias is real (fixture 10) | T23 | Confirmed (alias only) |
| NC-014 | P2 | Confirmed | dedupe key is raw timestamp string | T23 | Confirmed |
| NC-015 | P2 | Confirmed | connectivity.py:84 `add_edge` creates undeclared nodes | T25 | Confirmed |
| NC-016 | P3 [P2] | Confirmed | app.js:436-437 root (amber) selector after bad (red) | T26 | Confirmed |
| NC-017 | P2 | Confirmed | app.js:134 any non-normal/non-critical report → "Degraded" | T26 | Confirmed |
| NC-018 | P2 | Confirmed | app.js:144-146 insufficient → P3 | T26 | Confirmed |
| NC-019 | P2 | Confirmed | app.js:360 onerror closes stream, no retry/poll (matters on Render cold start) | T26 | Confirmed |
| NC-020 | P3 [P2] | Confirmed | `/stream` uses `safe()` not `run_dir()` → 200 + done for unknown id | T25 | Confirmed |
| NC-021 | P2 | Confirmed | only devices/samples capped; nodes/links/services unbounded | T25 | Confirmed |
| NC-022 | P2 | Confirmed | renderSamples → one row per sample (≤ 50k) | T26 | Confirmed |
| NC-023 | P3 [P2] | Confirmed | `state.failAgent` never cleared after run; CLI says "next run" | T26 | Confirmed |
| NC-024 | P3 [P2] | Confirmed | app.js:751 "no conflicts" ignores validation issues | T26 | Confirmed |
| NC-025 | P2 | Confirmed (merged into NC-002) | per-kind cap 0.25; fixed by quarantine + valid-coverage gate | T23 | Confirmed |
| NC-026 | P3 [P2] | Confirmed | index.html:144 "SWARM CONSENSUS" shows report.confidence | T26 | Confirmed |
| NC-027 | P3 [P2] | Confirmed | app.js:890 "Probability" label on heuristic score | T26 | Confirmed |
| NC-028 | — [P3] | **Rejected** | evidence tiles set `data-searchable` (app.js:805) and `renderSearch` selects `[data-searchable]` (app.js:254). Re-check in browser in T27 | — | Rejected |
| NC-029 | P3 | Confirmed | app.js:745 "CORRELATION HASH" shows run_id | T26 | Confirmed |
| NC-030 | P3 | Confirmed | PIPELINE_AGENTS lists 5, reporter missing | T26 | Confirmed |
| NC-031 | P3 | Confirmed | index.html:170 no synthetic-data notice | T26 | Confirmed |
| NC-032 | P3 | Confirmed | server.py `if scenario … elif upload_id` | T25 | Confirmed |
| NC-033 | P3 | Confirmed | `/api/runs` lists only dirs with report.json/state.json; fatal.txt-only dirs vanish | T25 | Confirmed |

Totals: 30 confirmed, 2 partially confirmed (NC-008 remedy, NC-013 claim), 1 rejected (NC-028).

## Design decisions (S13)

- **D1 Time window**: reference = `collected_at` (fallback newest sample). Never wall clock — bundled data is dated 2026-10-31 and judges run later. Samples older than ref − 1 h (stale) or newer than ref + 5 min (future) are quarantined, listed in `limitations` and data_quality, and excluded from analysis.
- **D2 Quarantine**: impossible values (pct outside 0–100, negative counts/latency, rssi > 0) become `None` in the analysed telemetry; each is recorded `{device_id, field, value, reason}` in a `quarantined` list surfaced in report limitations. Confidence is gated by valid-metric coverage.
- **D3 Same-instant merge**: duplicate key = (device_id, UTC instant). Non-conflicting fields merge; conflicting fields → field set to `None` + `conflict` issue.
- **D4 Alias conflict**: canonical + alias both present and converted values differ > 1 % → field `None` + `ambiguous` issue. Result independent of key order.
- **D5 Locality**: a multi-metric signature (e.g. congestion = util + latency/loss/drops) is scored per device (plus its direct topology neighbours) within one time bucket (± 5 min). Best candidate location wins. If supporting metrics only come from unrelated devices/times, confidence is capped below the diagnosed gate with limitation "evidence from unrelated devices/times".
- **D6 Normal**: normal uses the worst device grade. Any warning-grade device caps normal confidence below 0.75 and names that device in limitations.
- **D7 Alternatives**: higher-raw candidates overruled by a domain rule are listed with `overruled_by: <rule>`. Supersedes the T17 acceptance line.
- **D8 Access**: server sets an HttpOnly, SameSite=Lax session cookie; each run/upload records its owner; list/stream/report/details/remediate return 404 for other sessions. Stream redacts raw input to a size note. CLI/MCP unaffected; `/?run=` deep link works in the owner's browser.
- **D9 Uploads**: per-IP rate limit, total upload cap (count + bytes), cleanup on upload. Malformed JSON is still accepted (normalizer reports ERROR).
- **D10 Chart**: newest 200 points.
- **Gold labels**: the 16 scenarios must stay 16/16. Any intended label change needs a written reason here.

## Matrix execution log

T27 fills `docs/qa/TEST_MATRIX.md` Result column (Pass / Fail→NC-0xx / N/A + reason). New defects found while executing get IDs NC-034+ in the table above.
