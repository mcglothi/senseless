# Findings

Dated observations from real hardware. Newest at the bottom. Each one says what was seen and how,
so it can be re-checked.

## 2026-10-15: Microwave signature (labeled ground truth)

Microwave run for 30 s at a known time. Sanitized capture: [`samples/microwave-30s.jsonl`](../samples/microwave-30s.jsonl).

```
L2  +299 W   start_frame 25796520   fan / light / turntable
L2  +1726 W  start_frame 25796610   magnetron, 90 frames (1.5 s) later
L2  −1907 W  start_frame 25798290   off, single step
```

- 120 V load: L2 only, nothing on L1.
- **Two-stage turn-on** (small step, then big step 1.5 s later) is a usable fingerprint even without
  high-rate data. The steps sum to the off step (299 + 1726 ≈ 2025; drift accounts for the rest).
- On-to-off = 1770 frames = 29.5 s, which is how we confirmed `frame` is an AC-cycle counter.

### How Sense's own outputs compare

- `devices[]` named it "Microwave" within ~1 s of the first step. Good classification.
- But its per-device `w` **lagged**: it reported ~273 W for ~3 s while real draw was ~2000 W. The deltas
  are exact; Sense's attribution is smoothed. Build on deltas.
- The `new_timeline_event` ("Microwave turned on") arrived **38 s** after the event, and its `time` field
  was **~10 s earlier** than the actual start. Don't use timeline timestamps for timing.

## 2026-10-15: Things the Sense app hides

From 5 minutes of feed in one house:
- Only 3 devices appeared in `devices[]` (Always On, Other, one light). Everything else was lumped into "Other".
- A ~45 W load on L2 cycling on and off every 4–8 s (±40–47 W deltas). Unattributed.
- A ~56 W load on L1 on for ~12 s. Unattributed.
- Heavy leg imbalance: ~90–120 W on L1 vs ~1100 W on L2, and the ~915 W "Always On" is almost all on L2.
  Per-leg data (`channels`) narrows down which breakers to investigate. The Sense app doesn't show it.
- Delta volume: 41 in 5 min (32 on L2, 9 on L1), 16 of them under 30 W.

## 2026-10-15: Client app facts

- Android app `com.sense.androidclient` 2026.14-10839: Kotlin, Jetpack Compose, Ktor + OkHttp.
- **No certificate pinning** and no `network_security_config`. The app trusts system CAs only, so traffic
  capture needs either a rooted device/emulator with a system CA, or a repacked APK that trusts user CAs.
- "Watt Check": a measure-one-device-against-a-steady-baseline feature, present but hidden behind a
  debug flag (`debug_enable_wattcheck`), endpoint `devices/user_device_measurement/{id}`.
- Firebase project `sense-consumer-app` is used for push / remote config, not the data path.

## 2026-10-15 (evening): 240 V loads confirmed, Sense's clock runs ~10 s behind

- **240 V appliances do step both legs in the same AC cycle.** Seen 3 times in one evening:
  `+1,173 / +1,162 W`, `+1,217 / +1,232 W` (same `start_frame` on channel 0 and 1, ~equal size), and
  matching two-leg drops (`−400 / −409 W`). Detected in the UI by pairing deltas with the same frame,
  opposite legs, same sign, sizes within 25%. Answers the open question in [open-questions.md](open-questions.md).
- **`epoch` in `realtime_update` lags local receive time by ~9.6 s** (measured against an NTP-synced Mac:
  `ts − recv_ts = −9.6 s`). The same ~10 s offset showed up in the timeline event earlier, so it's Sense's
  clock, not ours. The collector stores both (`ts`, `recv_ts`); use `frame` for relative timing.
- Recurring patterns from one evening of deltas, all unlabeled for now: a ~495 W L2 load running ~3 min
  (seen 20:51 and 22:08), a 45 W step on L2 every few seconds (300+ per day), and a ~55 W, ~13 s pulse on L1.
  Steps under ~30 W are frequent enough to be mostly noise floor.

## 2026-10-16: Sense history API

- `GET app/monitors/{id}/history/usage?scale=DAY|WEEK|MONTH|CYCLE|YEAR&start=<ISO>` (from the Android client;
  `HOUR` is rejected). Returns `consumption.usage_breakdown_kwh` / `cost_breakdown_cents` (24 hours for DAY,
  one per day for MONTH, 12 for YEAR), totals, and `device_breakdown[]` with each detected device's own
  per-bucket kWh, cost and percentage.
- **A `start` at local midnight returns the previous period.** Anchor at local noon of the wanted date.
- Period starts come back at 05:00Z (the EST offset) even during EDT, so Sense's "day" can be shifted by an
  hour from a local day.
- For this house, October so far: 605 kWh, of which "Always On" 49% and "Other" 40%; Sense names ~11%.

## 2026-10-16: Missed offs can become hours-long mis-pairs

- **Verified by replaying local deltas and checking per-leg readings:** the newest-compatible-on
  rule still leaves older unmatched ons eligible after newer cycles close. Later unrelated drops
  can close those stale ons hours later. Some such intervals have sustained leg power below the
  putative load's on-size, disproving continuous operation.
- Pairing now retires candidates contradicted by sustained power, including impossible overlap
  with a newer completed similar cycle (see [detection.md](detection.md)). Synthetic regressions
  preserve supported long loads and overlapping identical loads; reverting the fix reproduces
  a false five-hour closed run. No local captures were added to the repository.
- **Limit:** this is a feasibility check, not appliance identity. High background power, missing
  readings and starts before the evidence window can leave ambiguous long pairs intact.

## 2026-10-16: Pattern durations catch short-repeaters' long mis-pairs

- **Verified by replaying the local exports:** the sustained-power check still allowed a
  14,741 s L1 ~56 W pairing, despite that pattern's ~13 s median closed duration. Background
  consumption can support the putative load for hours without proving its identity.
- After assignment, unnamed patterns with at least eight closed runs now reject candidates
  exceeding both 20x the other runs' median and 4x their 95th percentile. Recompute the statistics
  after rejections. Discard the claimed off and mark the run unresolved; no end is invented.
- On the supplied replay, runs over 30 minutes fell **23 -> 19** relative to round 1; ten closed
  duration outliers were rejected overall. To reproduce the round-1 baseline, restore the eleven
  on-steps preceding the delta export from the saved runs, then replay with the supplied readings.
  The replay used fresh unnamed patterns; the exports do not include saved pattern names.
- **Heuristic, not hardware ground truth:** synthetic fixtures preserve normally 20–90 minute
  histories, sparse histories and named patterns. Removing the new recompute call makes the
  synthetic four-hour repeater regression fail. Sparse/broad histories still allow some long
  closures; no raw account captures were added to the repository.


## 2026-10-16: Provider backend preserves the CMP model

- **Verified by synthetic tests, not new utility traffic:** pricing now runs through a validated CMP
  JSON pack, retaining $157.13 at 550 kWh and $362.71 at 1,350 kWh. All prices in the compatibility
  catalog derive from the pack. The Electric Technology expiry is recorded as 2027-01-31.
- Interval imports and analysis use `usage_interval` in settings.db. Migration preserves old meter
  rows without overwriting corrections; finer observations replace overlapping portions of coarser
  ones, and incomplete hours do not count as complete days. Synthetic DST/overlap cases cover this.
- Green Button XML, CSV profiles, saved mappings, CMP bill parsing, normalized lines, preview and
  tariff/mapping endpoints are implemented. Synthetic identity sentinels are absent from persisted
  data, responses and errors. No household captures were added.
- **Still unverified:** Smart Meter Texas and Green Button CSV against real exports. Local timestamps
  during DST gaps/repeated hours need explicit offsets. No jurisdiction-specific holiday calendar is
  inferred. The engine honors explicit holiday flags; CMP keeps its prior holiday approximation.

## 2026-10-16: Washer fragments form one program activity envelope

- **Verified against local traffic by replay through a temporary SQLite database:**
  3,898 deltas plus 41,862 readings produce one L1 cycle, 08:19:38–09:06:00,
  122 candidate steps, 219.18 Wh above the two-minute pre-burst baseline.
  The labeled washer starts approximately 08:21; the grouping includes an earlier
  nearby step, so these are inferred activity boundaries, not measured appliance offs.
- A 180 s gap split this wash at a 210 s pause; 240 s joins it. At least half
  the candidate steps must lack reliable closed runs, alongside the chatter
  exclusion and sustained power rise. The 5,982 W and 5,601 W late both-leg
  dryer runs stay closed runs outside cycles; no L2/both or background-only
  cycles appear. Seventeen fragment patterns meet the 70% cycle-membership flag.
- Names and cycle patterns persist in `settings.db`. Repeat recomputation
  after naming preserves the cycle ID and one pattern. See [cycles.md](cycles.md)
  for rules, full replay results and limits; no raw capture was added to the repo.
- **Synthetic only:** dishwasher heater stages, other leg configurations and
  multi-day clustering. Removing chatter exclusion admits a deliberately noisy
  relay fixture as a false cycle; with the exclusion it yields none.

## 2026-10-16: Cycle review regressions and replay after isolation fixes

- **Verified by rerunning the same scratch capture through temporary SQLite:**
  still exactly one L1 envelope, 08:19:38–09:06:00, 219.18 Wh; no dryer,
  L2/both-leg or chatter cycles. Candidate steps are now 120 and supporting
  unclosed members 109; thirteen public patterns meet the fragment flag.
- The six-hour expiry pipeline returns 1,369 public runs, versus 1,465 retained
  cycle-evidence runs. Public runs and persistent pattern IDs/counts/centres
  match a separate legacy-pipeline replay exactly, apart from cycle membership.
  Expanded evidence never creates or updates persistent run patterns.
- **Synthetic regressions, individually falsified by reverting each fix:** saved
  names/ignores survive a five-to-forty-minute envelope and detector restarts;
  clean recurring closures cannot bridge two programs; independent closed,
  named, small and chatter runs retain inbox eligibility; expired missed offs
  cannot return through run timelines, pattern counts or load statistics.
- **Heuristic:** reliable repeated closures mean at least three and 80% closed
  runs in a pattern. Removing all individual closures splits this wash, whose
  mixed program patterns include incidental pairings across a pause. Membership
  nevertheless uses only unclosed supporting candidates. See [cycles.md](cycles.md).

## 2026-10-16: Bounded shared-off pairing replay (round 2)

- **Verified against the same local scratch traffic through `Detector` and
  temporary SQLite databases:** 5,655 deltas over 02:33:39–14:33:08 and
  45,462 readings over 08:03:02–14:33:08. Compared `SHARED_OFF_CAP=0` with
  12, preserving settled-level sizing. No captures or account identifiers were
  added to the repository. Appliance labels below are supplied by the operator.
- **The requested pool recovery is falsified on these captures.** The −1,054.2 W
  off at 14:28:10 already closes a power-supported **1,078.5 W** single started
  at **13:05:12**, both with shared offs enabled and disabled. The mandated
  single-match priority prevents pair fallback at this off.
- Independently, the salt start is merged from +61.1 W at **08:31:53** and
  +475.7 W two seconds later. Its settled window has no coverage: the recordings
  stop at 08:31:59 and resume at 08:33:16. Existing sizing therefore correctly
  retains **536.9 W**, not the operator's roughly 280 W estimate. That run closes
  singly at **13:05:23** (−556.1 W), long before the timer off. A 901.1 + 536.9 W
  pair would not satisfy the unchanged summed-size tolerance anyway.
- The **901.1 W** pool start at **08:31:00** remains unresolved and expires
  from public runs after six hours. At the timer off it is **299th newest**
  among **305** indexed open L2 starts, outside the fallback's 12-run cap.
  Its hypothetical duration to 14:28:10 is 21,429.5 s, or **5,364.0 Wh**;
  that is a labeled expectation, **not recovered runtime/energy**. Recovering
  this example needs a separately validated refinement to sizing, competing
  single matches and candidate retrieval; compound offs alone do not establish it.
- Raw pairing produces **44 shared-off run closures at 22 off events**.
  Duration-outlier rejection removes one closure and clears its provenance;
  `Detector` exposes **43 surviving shared-off runs at 22 events**. Public run
  count changes **1,696 → 1,704**; disabling the fallback yields zero shared
  closures and restores the baseline. Each shared run exposes
  `closed_by="shared-off"` and keeps the full observed off watts.
- **Spot checks, not appliance identifications:** L1 starts at 14:31:12 and
  14:31:15 (37.9 + 91.7 = 129.6 W) close at 14:31:28 (−129.4 W); covered
  pre/post leg means fall 126.8 W, a plausible shared switch. L2 11:05:39 and
  11:06:57 (133.8 + 1,146.3 = 1,280.1 W) close at 11:07:25 (−1,463.4 W),
  within 192.0 W tolerance; covered means fall 1,234.3 W. Two small L1 pairs
  closing at 08:44:59 and 08:45:19 also fit step sizes, but covered pre/post
  means rise 8.6 and 25.4 W amid program chatter. Those are questionable
  attributions: absolute power floors cannot establish load identity against
  a changing background. Earlier overnight matches lack reading coverage.
- The probable **well pump is unchanged:** both legs, **793.1 W**,
  **13:13:12–13:15:33**, **140.5 s**, **30.95 Wh**. All **22 dryer-element
  runs** retain sizes, ends, statuses, durations and energy, **2,840.49 Wh**
  total. Fresh settings databases can allocate different numeric pattern IDs
  when additional old starts are recovered.
- Shared closure is weaker evidence of an individually repeatable load in
  cycle detection. Excluding shared closures from clean-closure/chatter tests
  preserves the washer's ID, **08:19:38–09:06:00** envelope, peak, duration
  and **219.18 Wh**. Fingerprint/membership metadata is not identical: candidates
  **115 → 116**, step rate **2.4802 → 2.5018/min**, median candidate size
  **441.81 → 441.50 W**, and supporting members **108 → 109**. Without this
  safeguard the end shifts 16 s earlier and energy drops to 216.38 Wh. No new
  L2/both program envelopes appear.
- **Validation:** 18 synthetic shared-off cases plus a cycle-envelope regression;
  focused shared-off/cycle suites **52 passed**. Disabling the fallback makes
  all **7 selected positive shared-off regressions fail**. Full requested
  `uv sync -q && .venv/bin/python -m pytest -q` (with a writable temporary uv
  cache) yields **526 passed, 12 failed**, all failures from sandbox-denied
  socket binds, matching the preceding round's environment limitation. Failed
  tests are `test_api.py::{test_stream_delivers_snapshot_then_events,
  test_shutdown_is_prompt_with_an_open_stream,test_series_and_deltas_accept_windows,
  test_circuits_api}`, `test_billing.py::{test_billing_api,
  test_today_cost_uses_the_tariff}`, `test_detect.py::test_loads_api_name_and_ignore`,
  `test_floorplans.py::{test_http_api,test_http_write_security_and_upload_limits}`,
  `test_history.py::test_history_endpoint`, and
  `test_hunt.py::{test_hunt_api_long_poll_and_label_to_room,
  test_stream_snapshot_carries_hunt}`.
- **Benchmark:** supplied `scratchpad/perf/bench.py`, **333,832 readings /
  22,864 deltas**, **1.17 / 1.19 / 1.19 s**, maximum RSS approximately
  **108 MB**. Each unmatched off examines at most 66 pairs; the ordered index
  avoids scans through thousands of unresolved or previously closed starts.

## 2026-10-16: Settled-level inrush sizing replay

- **Verified against local scratch traffic by replay through `Detector` and a
  temporary SQLite database:** 5,655 deltas and 45,462 readings, ending 14:33:08
  local time. No raw data or account identifiers were copied into this repository.
  The appliance labels below come from the task's examples; the well-pump identity
  remains probable, not established by the detector.
- The 13:13:12 both-leg start changes from 1,706.7 W, unresolved, zero known
  runtime/energy to **793.1 W**, closed at 13:15:33, **140.5 s and 30.95 Wh**.
  Its reported 1,706.7 W is retained as `inrush_w`. The overlap rule uses exact
  `L1`/`L2`/`both` categories, like run pairing; a single-leg step is not a `both`
  step for that guard.
- The 08:31:00 L2 pool start changes from 2,230.3 W to **901.1 W**, with
  `inrush_w=2230.3`. The requested windows give 919.8 W before and 1,820.9 W
  settled. It **still does not close** against the approximately −1,054 W off
  at 14:28:10: the mismatch is about 153 W versus a 135 W tolerance. In both
  replays the unresolved start has expired out of public runs at 14:33; retained
  cycle evidence exposes it for diagnosis. Thus the rule alone does not recover
  the example's expected roughly 6 kWh. Pairing tolerance was not widened.
- The earlier 08:30:25 merged L2 start (1,271.2 W including its small first
  stage) adjusts to 93.9 W and pairs with a later small off. That is an example
  of the heuristic's weakness: background changes can create an apparent rise
  without establishing that a device really ran. The salt-cell candidate remains
  536.9 W with no inrush and its prior 13:05:23 closure; this is not a verified
  appliance attribution either.
- Public run count is **1,696 before and after**; **10** public runs adjust,
  or **12** starts when expired unresolved cycle evidence is included. All
  **22** both-leg dryer-element runs (5.6–6.0 kW; 21 closed, one already
  unresolved) retain sizes, ends, statuses, duration and energy: 2,840.49 Wh
  total. Their `inrush_w` values remain null.
- The washer still forms one L1 envelope, **08:19:38–09:06:00, 219.18 Wh**,
  with the same ID, peak and duration. It is not completely unchanged: one
  newly pairable fragment reduces candidates **116 → 115**, supporting
  unclosed members **109 → 108**, step rate **2.5018 → 2.4802/min**, and
  median candidate size **442.29 → 441.81 W**. No L2/both cycles appear.
- **Synthetic validation:** motor surges on either leg and 240 V, clean
  resistive loads, staged startup, overlap/coverage boundaries, missing/nonfinite
  data, generation isolation, SQL/raw parity, pattern medians and old settings
  migration. Disabling settled sizing makes **12 of 29** new regressions fail.
  The full suite has **507 passes** and **12 sandbox socket-bind failures**
  (`PermissionError` at `127.0.0.1` bind), with no other failures. The clean
  cycle-bridge fixture now starts its independent load away from unreported
  program power boundaries, preserving that fixture's clean-start assumption.
- **Benchmark:** the supplied `scratchpad/perf/bench.py`, eight replay copies,
  **333,832 readings / 22,864 deltas** over the 48-hour detection window.
  Before: **1.16 / 1.15 / 1.14 s** per recompute. After:
  **1.16 / 1.15 / 1.18 s** (median **1.16 s**). Targeted one-second SQL means
  preserve the existing five-second cycle-energy bins; memory was approximately
  111 MB maximum RSS. These timings are local measurements, not a performance
  guarantee on other hosts.

## 2026-10-16: Baseline-relative evidence replay (round 3)

- **Verified by replay of the same scratch traffic through `Detector`:**
  5,655 deltas, 45,462 readings, ending 14:33:08 local. Baseline evidence now
  uses the covered one-second mean over first-stage −12 s to −2 s for every
  on, including small/overlapped starts ineligible for surge sizing. Reject a
  sustained five-second maximum below `pre + 0.85*w - 300 W`; fall back to
  the previous absolute floor without a covered baseline. Single and shared
  matches use this rule. Combined overlap checks use the older baseline once.
  Surge-sized singles allow 20% off mismatch (plain runs retain 15%); shared
  sums retain 15%, and their starts must be within 600 frame-derived seconds.
- **Pool pump recovered:** the 08:31:00 L2 start, **901.1 W** with
  `inrush_w=2230.3`, closes **singly at 14:28:10** against −1054.2 W.
  Duration **21,429.5 s (5 h 57 m 9.5 s)**; energy **5364.02 Wh**. These
  captures' specified pre-window averages **919.8 W**, rather than the rough
  983 W estimate; required floor **1385.7 W**, observed sustained minimum
  **1821.2 W** before the candidate off. No contradiction occurs.
- The competing **13:05:12 L2 1078.5 W** start becomes **unresolved**, with
  no invented end and zero known runtime/energy. Its pre is **2116.0 W**,
  required floor **2732.8 W**, versus a sustained minimum **2088.5 W**.
  The partial offs are not combined. The putative salt start at 08:31:53
  (merged with 08:31:55, still 536.9 W because settled coverage is missing)
  also becomes unresolved and expires from public runs; its old 13:05:23
  closure is rejected. Appliance labels remain operator-supplied identities.
- **Well pump unchanged from round 2:** both-leg 13:13:12–13:15:33,
  **793.1 W, 140.5 s, 30.95 Wh**, −765.2 W off. All **22** dryer-element
  runs retain their wattage, end, status, duration and energy versus both main
  and round 2: **2840.49 Wh** total, 21 closed and one unresolved.
- **Washer envelope/energy unchanged:** L1 **08:19:38–09:06:00**, same
  cycle ID, peak and 2782 s duration, **219.18 Wh**. Fragment metadata does
  change: candidates **116 → 117**, step rate **2.5018 → 2.5234/min**,
  median size **441.50 → 441.18 W** versus round 2, supporting membership
  **109 → 112**. No additional L2/both program envelopes appear.
- **Shared offs decrease less than hoped:** raw pairing **44 → 38 closures**
  at **22 → 19 off events**. One duration outlier loses its closure/provenance,
  leaving **37 public shared-off runs at 19 events** (round 2: 43 at 22).
  Most surviving pairs already start within ten minutes; nearby starts and
  300 W slack still admit program chatter. Spot checks using covered means
  over off −12..−2 s and +6..+20 s:
  - L1 14:31:12 + 14:31:15, **129.6 W**, off 14:31:28 **−129.4 W**:
    mean falls **126.8 W**, plausible shared switching.
  - L2 11:05:39 + 11:06:57, **1280.1 W**, off 11:07:25 **−1463.4 W**:
    mean falls **1234.3 W**, plausible within summed-size tolerance.
  - L1 offs 08:44:59 and 08:45:19 remain questionable: pair sums
    **130.8 / 143.4 W** match drops **−141.0 / −140.4 W**, but covered
    means instead rise **8.6 / 25.4 W** amid washer chatter. These are weak
    associations, not confirmed individual appliances.
- **Versus main (`c436a3d`), matching existing public starts by generation,
  frame and legs:** **153** change status/end: 75 closed → unresolved,
  44 unresolved → closed, 27 closed with a different end, four running →
  closed, three running → unresolved. Another run changes provenance only
  (11:06:57 L2, same 11:07:25 end), making **154** if provenance counts.
  Ten starts newly remain public, including the pump, while nine formerly
  closed starts become unresolved and expire; total **1696 → 1697**.
  Versus round 2, 133 common public starts change status/end (1704 → 1697).
  Notable changes besides the pumps/salt: both-leg 09:00:15 becomes a
  **717.3 W** run closed 09:06:24; L2 **2604.4 W** at 13:20:25 and
  **433.9 W** at 13:27:59 lose questionable later closures; L1 small shared
  runs at 14:31:12/15 now close 14:31:28. Full comparison JSON and scripts
  remain in the external `scratchpad/inrush/` directory; no raw capture or
  account identifiers were added to the repo.
- **Validation:** 33 added synthetic cases cover relative floors, exact
  threshold/coverage boundaries, each leg category, absolute fallback, SQL/raw
  parity for small/overlapped starts, baseline double-counting, surge-only
  20% boundaries and the 600 s frame boundary. Mutation falsification produces
  **9 failures** when relative evidence is disabled, **3 failures** when surge
  tolerance returns to 15%, and **1 failure** when the start-gap gate is
  removed. Focused detector/cycle suites: **214 passed**. Requested full
  `uv sync -q && .venv/bin/python -m pytest -q` (temporary writable uv cache):
  **559 passed, 12 failed**, the same sandbox-denied localhost socket binds
  as before, with no other failures. AIKB instruction readiness validation
  passes when run from the AIKB root.
- **48 h benchmark:** supplied `scratchpad/perf/bench.py`, **333,832 readings /
  22,864 deltas**, recompute **1.07 / 1.10 / 1.10 s**, maximum RSS **132 MB**.
  An initial 1.95–2.17 s version allocated a temporary dataclass for every
  older-overlap check; passing combined watts directly removes that allocation
  while preserving the same matching results. All final timings are below 2 s.
