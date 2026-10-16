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
