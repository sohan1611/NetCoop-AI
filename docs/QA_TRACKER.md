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
| 01 disconnected_signals | **congestion 1.0** | not a confident single-device congestion; limitation "evidence from unrelated devices/times" |
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
| NC-001 | P0 | Confirmed | fixture 01 → congestion 1.0 from AP-A util + HOST-C latency; correlator maxes each metric fleet-wide | T24 | Tested (T24: `test_nc001_signature_requires_local_support`) |
| NC-002 | P0 | Confirmed | fixture 02 → congestion 0.9; `_validate_rows` flags but keeps values | T23 | Tested (T23: `test_nc002_impossible_values_are_itemized_and_excluded`, `test_nc002_each_impossible_metric_becomes_none`) |
| NC-003 | P0 [P1] | Confirmed | fixtures 03/07/11 → congestion 0.83–1.0; cutoff = batch max − 1 h, stale rows kept, no future check | T23 (window) + T24 (time join) | T23 window tested (`test_nc003_collected_at_rejects_stale_and_future_samples`, `test_nc003_time_window_boundaries_and_newest_sample_fallback`); T24 time join tested (`test_nc003_signature_requires_same_time_bucket`) |
| NC-004 | P1 | Confirmed | fixture 04 → 1 sample; dedupe key drops 2nd row (normalizer.py:735-753) | T23 | Tested (T23: `test_nc004_same_instant_merges_complementary_fields_and_nulls_conflicts`) |
| NC-005 | P1 | Confirmed | fixture 05 results differ by key order | T23 | Tested (T23: `test_nc005_alias_conflicts_are_order_independent`, `test_nc005_converted_aliases_and_canonical_precedence`) |
| NC-006 | P1 | Confirmed | fixture 06 → normal 0.7 with FAULTY at 110 ms / 2.5 % loss | T24 | Tested (T24: `test_nc006_normal_uses_worst_device`) |
| NC-007 | P1 | Confirmed | `/api/runs` + `/stream` + report/details have no ownership check; stream replays raw input | T25 | Tested (T25: tests/test_api.py) |
| NC-008 | P2 [P1] | Confirmed (partial remedy) | upload never calls `admit()`, cleanup only on `/api/run`. Downgraded: 5 MB cap + TTL exist. Remedy "reject non-JSON early" **rejected**: malformed input must reach the normalizer (PS interop demo) | T25 | Tested (T25: tests/test_api.py) |
| NC-009 | P1 | Confirmed | details.py:105 `points[:MAX_SERIES]` keeps oldest 200 | T26 | Tested (T26: browser + tests) |
| NC-010 | P1 | Confirmed | app.js:73 CRIT counts confidence ≥ 0.75 incl. normal | T26 | Tested (T26: browser + tests) |
| NC-011 | P2 [P1] | Confirmed | app.js:67 `incidentKey` prefers static incident_id. UX only (per-browser localStorage) | T26 | Tested (T26: browser + tests) |
| NC-012 | P2 | Confirmed | correlator.py:924-931 keeps only `confidence < winner`. Reverses T17 rule "no alternative ≥ chosen" — transparency wins; show with `overruled_by` | T24 | Tested (T24: `test_nc012_domain_overruled_alternatives_remain_visible`) |
| NC-013 | P3 [P2] | Partially confirmed | Claim that bundled scenarios use `version` is **false** (they use `schema_version`, normal.json:176). Missing `version` alias is real (fixture 10) | T23 | Tested (T23: `test_nc013_version_alias_has_full_quality`) |
| NC-014 | P2 | Confirmed | dedupe key is raw timestamp string | T23 | Tested (T23: `test_nc014_dedupe_uses_utc_instant_not_timestamp_spelling`) |
| NC-015 | P2 | Confirmed | connectivity.py:84 `add_edge` creates undeclared nodes | T25 | Tested (T25: tests/test_api.py) |
| NC-016 | P3 [P2] | Confirmed | app.js:436-437 root (amber) selector after bad (red) | T26 | Tested (T26: browser + tests) |
| NC-017 | P2 | Confirmed | app.js:134 any non-normal/non-critical report → "Degraded" | T26 | Tested (T26: browser + tests) |
| NC-018 | P2 | Confirmed | app.js:144-146 insufficient → P3 | T26 | Tested (T26: browser + tests) |
| NC-019 | P2 | Confirmed | app.js:360 onerror closes stream, no retry/poll (matters on Render cold start) | T26 | Tested (T26: browser + tests) |
| NC-020 | P3 [P2] | Confirmed | `/stream` uses `safe()` not `run_dir()` → 200 + done for unknown id | T25 | Tested (T25: tests/test_api.py) |
| NC-021 | P2 | Confirmed | only devices/samples capped; nodes/links/services unbounded | T25 | Tested (T25: tests/test_api.py) |
| NC-022 | P2 | Confirmed | renderSamples → one row per sample (≤ 50k) | T26 | Tested (T26: browser + tests) |
| NC-023 | P3 [P2] | Confirmed | `state.failAgent` never cleared after run; CLI says "next run" | T26 | Tested (T26: browser + tests) |
| NC-024 | P3 [P2] | Confirmed | app.js:751 "no conflicts" ignores validation issues | T26 | Tested (T26: browser + tests) |
| NC-025 | P2 | Confirmed (merged into NC-002) | per-kind cap 0.25; fixed by quarantine + valid-coverage gate | T23 | Tested (T23: `test_nc025_valid_coverage_gates_confidence_despite_penalty_cap`) |
| NC-026 | P3 [P2] | Confirmed | index.html:144 "SWARM CONSENSUS" shows report.confidence | T26 | Tested (T26: browser + tests) |
| NC-027 | P3 [P2] | Confirmed | app.js:890 "Probability" label on heuristic score | T26 | Tested (T26: browser + tests) |
| NC-028 | — [P3] | **Rejected** | evidence tiles set `data-searchable` (app.js:805) and `renderSearch` selects `[data-searchable]` (app.js:254). Re-check in browser in T27 | — | Rejected |
| NC-029 | P3 | Confirmed | app.js:745 "CORRELATION HASH" shows run_id | T26 | Tested (T26: browser + tests) |
| NC-030 | P3 | Confirmed | PIPELINE_AGENTS lists 5, reporter missing | T26 | Tested (T26: browser + tests) |
| NC-031 | P3 | Confirmed | index.html:170 no synthetic-data notice. S17: reopened (T26 text did not warn uploaders); upload notice added in T28 | T28 | Tested (T28) |
| NC-032 | P3 | Confirmed | server.py `if scenario … elif upload_id` | T25 | Tested (T25: tests/test_api.py) |
| NC-033 | P3 | Confirmed | `/api/runs` lists only dirs with report.json/state.json; fatal.txt-only dirs vanish | T25 | Tested (T25: tests/test_api.py) |

### Found in T27 (final QA pass)

| ID | Pri | Evidence (T27) | Status |
|---|---|---|---|
| NC-034 | P2 | matrix A05/S12: `conflicting_evidence` report listed only `interface fault 24.5 %` as alternative while weak_wifi 0.931 / device_overload 0.906 led (visible only in limitations); capped confidence hid them | Tested (T27: tests/test_qa_t27.py) |
| NC-035 | P3 | matrix A13: `overruled_by` existed in the correlator but the report's alternative text did not say a domain rule overruled it | Tested (T27: tests/test_qa_t27.py) |
| NC-036 | P2 | matrix N20: duplicate device_id rows with `degraded` and `down` produced no data-quality issue | Tested (T27: tests/test_qa_t27.py) |
| NC-037 | P2 | matrix H22/H24: worker crash (fatal.txt only) → `GET /report` returned HTTP 500 "run produced no report" | Tested (T27: tests/test_qa_t27.py) |
| NC-038 | P3 | matrix U19: console `run bogus` hint printed humanized names ("Device overload") that `run` rejects | Tested (T27: browser) |
| NC-039 | P2 | matrix U26/U27: `?run=<unknown or expired>` looped "Stream interrupted / Waiting for report" forever and left `state.running` true, blocking Trigger and Run until reload | Tested (T27: browser) |
| NC-040 | P3 | matrix S15: `evidence from unrelated devices/times` (about a capped congestion candidate) appeared in a report whose winner was latency_degradation, reading as a flaw of the diagnosis | Tested (T27: tests/test_qa_t27.py) |
| NC-041 | P3 | matrix U31: `--dim` label colour #5b6880 on #131d2e = 3.0:1 (< WCAG AA 4.5:1) for 9–10 px labels; raised to #7d8aa3 (≈4.8:1) | Tested (T27: browser contrast probe) |
| NC-042 | P1 | S17 verification: T23 counts out-of-window samples as invalid metrics; congestion.json + 4 h healthy history → insufficient_evidence 0.165 (plain 0.942). Real uploads with history lose the diagnosis | Tested (T28: tests/test_qa_t28.py; S17 rerun) |
| NC-043 | P2 | S17: T25 `MAX_UPLOAD_FILES = 20` is global — 20 uploads/h from anyone → 413 for every visitor (judges) | Tested (T28: tests/test_qa_t28.py; S17 rerun) |
| NC-044 | P3 | S17: "evidence from unrelated devices/times" shown for single-device fixtures 02/11 (real reason: util missing/low); normal's "warning-grade devices" limitation leaked into every report (congestion 0.94) | Tested (T28: tests/test_qa_t28.py; S17 rerun) |
| NC-045 | P3 | S17: T28 deleted the upload before `admit()`; a 429 then lost the user's file | Tested (T29: tests/test_qa_t29.py; S17 browser: reused upload_id → 404 after run) |
| NC-046 | P3 | S17 live check: `nc_session` cookie set without `Secure` on the HTTPS deployment (Render terminates TLS, app saw http) | Tested (tests/test_qa_t29.py::test_nc046…; live re-check after deploy) |

### Independent re-audit of bc123e9 (docs/qa/NetCoop_AI_Reaudit_Oct31.md, local only) — verified in S17 on a clean checkout

| ID | Pri | Verdict + evidence (S17) | Status |
|---|---|---|---|
| RQA-01 | P1 | Confirmed. One device, 1 s spacing: 500/1k/2k/4k samples → 0.9/2.2/5.0/10.2 s. Cost is N × window (every distinct timestamp re-scores a ±300 s scope through `_observe_metric`), not the report's 2.5 G-op estimate | Tested (T29; S17: 4000 one-device samples 10.24 s → 0.27 s, linear; 5×3600 = 1.18 s) |
| RQA-02 | P2 | Confirmed. fixture 13 (unparseable timestamp) → latency_degradation 0.912 | Tested (T29; S17: fixture 13 → insufficient_evidence 0.203) |
| RQA-03 | P1 [P2] | Confirmed and raised: fixture 14 → 0.95; the same data without the 09:10 jitter row → 0.463. Bridging invents corroboration | Tested (T29; S17: fixture 14 → insufficient_evidence 0.299) |
| RQA-04 | P2 | Confirmed. details.py:208-211 drops `overruled_by`; UI cards say "Partial Contributor" | Tested (T29; S17 browser: org_hardware_failure cards "Overruled by: critical device status …") |
| RQA-05 | P2 | Confirmed. fixture 15 → normal 0.749 + "warning-grade devices", hero shows "Healthy" | Tested (T29; S17 browser: fixture 15 → "Normal with warnings" / Watch) |
| RQA-06 | P3 | Confirmed. `admit` records the rate-limit hit before the concurrency check | Tested (T29: tests/test_qa_t29.py) |
| RQA-07 | P3 | Valid: video link pending (T22); README "Share a run" now only reopens in the owner's browser; deploy SHA to verify after push | README wording Tested (T29); video link + deploy SHA → T22 |
ongestion 0.94) | Tested (T28: tests/test_qa_t28.py; S17 rerun) |

Totals: 30 confirmed, 2 partially confirmed (NC-008 remedy, NC-013 claim), 1 rejected (NC-028, re-checked in the browser in T27: evidence search works) from the external audit; plus 8 new defects (NC-034..NC-041) found and fixed in T27.

## Design decisions (S13)

- **D1 Time window**: reference = `collected_at` (fallback newest sample). Never wall clock — bundled data is dated 2026-10-31 and judges run later. Samples older than ref − 1 h (stale) or newer than ref + 5 min (future) are quarantined, listed in `limitations` and data_quality, and excluded from analysis.
- **D2 Quarantine**: impossible values (pct outside 0–100, negative counts/latency, rssi > 0) become `None` in the analysed telemetry; each is recorded `{device_id, field, value, reason}` in a `quarantined` list surfaced in report limitations. Confidence is gated by valid-metric coverage.
- **D3 Same-instant merge**: duplicate key = (device_id, UTC instant). Non-conflicting fields merge; conflicting fields → field set to `None` + `conflict` issue.
- **D4 Alias conflict**: canonical + alias both present and converted values differ > 1 % → field `None` + `ambiguous` issue. Result independent of key order.
- **D5 Locality**: a multi-metric signature (e.g. congestion = util + latency/loss/drops) is scored per device (plus its direct topology neighbours) in the same 5-minute bucket. Best candidate location wins. If supporting metrics only come from unrelated devices/times, confidence is capped below the diagnosed gate with limitation "evidence from unrelated devices/times".
- **D6 Normal**: normal uses the worst device grade. Any warning-grade device caps normal confidence below 0.75 and names that device in limitations.
- **D7 Alternatives**: higher-raw candidates overruled by a domain rule are listed with `overruled_by: <rule>`. Supersedes the T17 acceptance line.
- **D8 Access**: server sets an HttpOnly, SameSite=Lax session cookie; each run/upload records its owner; list/stream/report/details/remediate return 404 for other sessions. Stream redacts raw input to a size note. CLI/MCP unaffected; `/?run=` deep link works in the owner's browser.
- **D9 Uploads**: per-IP rate limit, total upload cap (count + bytes), cleanup on upload. Malformed JSON is still accepted (normalizer reports ERROR).
- **D10 Chart**: newest 200 points.
- **Gold labels**: the 16 scenarios must stay 16/16. Any intended label change needs a written reason here.

T23 status: DONE. 24 new regression cases (tests/test_qa_t23.py); legacy test_normalizer duplicate/stale test updated to D1/D3 (1 sample). pytest 222 passed, evaluate 16/16.

T24 status: DONE. Four regression functions / 10 cases in `tests/test_qa_t24.py` cover NC-001, NC-003, NC-006 and NC-012, including direct neighbours, the inclusive 5-minute join boundary, a 24-hour split, warning thresholds and raw-score preservation. Fault signatures and displayed evidence use the best device/neighbour/time scope; device/service snapshots use collection time. Congestion requires utilization plus local supporting metrics. Normal uses the worst health grade, retaining coverage penalties, and names warning-grade devices.

T17's alternative-confidence test and the matching stress-script invariant were deliberately changed: D7 requires retaining higher raw scores with `overruled_by` rather than hiding them. The evidence-deduplication unit test now supplies evidence within the selected scope.

Verification: `pytest -q` 232 passed (workspace `--basetemp`; system temp directory is inaccessible); `python scripts/evaluate.py --no-llm` 16/16; stress and hostile-input scripts no CRASH, with LLM disabled. No normalizer, UI or API changes.

| Fixture | T24 verified acceptance |
|---|---|
| 00 control_normal | normal, no critical supporting metrics |
| 01 disconnected_signals | low-confidence latency degradation; congestion below diagnosed gate; limitation "evidence from unrelated devices/times" |
| 06 warn_only_in_healthy_fleet | warning-level interface fault, no diagnosed normal/Healthy; FAULTY named in limitations |
| 11 signals_24h_apart | insufficient_evidence, not congestion; correlator-only time-join cases also pass |

## Matrix execution log

T27 fills `docs/qa/TEST_MATRIX.md` Result column (Pass / Fail→NC-0xx / N/A + reason). New defects found while executing get IDs NC-034+ in the table above.

T25 status: DONE. tests/test_api.py covers two-session isolation (list/stream/report/report.md/details/topology/remediate/upload → 404) and each NC id. pytest 241 passed, evaluate 16/16.

T26 status: DONE. Browser-checked (375 px no overflow, no console errors besides a self-inflicted 429 from rapid test runs): fixture 08 upload chart ends 500 ms, normal not CRIT/Healthy, insufficient_data = Unknown/Unclassified, Acknowledge All then new run stays unacknowledged, fail-agent sent once then cleared. pytest 242 passed, evaluate 16/16. Not browser-tested: SSE retry/poll (NC-019) and samples pagination (NC-022) beyond code/test review.

T27 status: DONE. All 117 matrix checks executed (Result column filled, 117/117 Pass after NC-034..NC-041 fixes); every tracker row is Tested or Rejected. pytest 247 passed, evaluate 16/16, hostile_inputs/stress_test no CRASH. Not covered: real print dialog render, screen reader, light-theme contrast (no light theme), live Render/Vercel deployment (local build only).

## Live deployment check (S17, 2026-10-31)

Vercel static = T29 (upload notice, "Normal with warnings", RUN ID). Render API = T29: fixtures 14/13 via Vercel → insufficient_evidence 0.299 / 0.203 (identical to local). Two cookie jars through the Vercel rewrite: session B gets `[]` and 404 on report/stream/details/report.md of A's run; both sources → 422. Browser on netcoop-ai.vercel.app: congestion 0.942, SSE 12 messages through Vercel, hero Critical / P1 CRIT, no console errors. Found NC-046 (cookie not Secure).
