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
