# Load detection

Senseless groups the monitor's on-board `deltas` into steps, runs and recurring
patterns. It does not classify raw current waveforms. The rules below are verified
by reading [`senseless/detect.py`](../senseless/detect.py), not by testing every
case on hardware. The microwave observations are verified against captured traffic.

## Deltas to steps

- `MIN_STEP_W = 30`: discard deltas with absolute size below 30 W. Exactly 30 W
  survives. This filter runs before any pairing or merging.
- Channel 0 is L1; channel 1 is L2. At each `start_frame`, take the first delta on
  each channel. Pair them as one `both` step if their signs match and their size
  difference, divided by the larger absolute size, is at most
  `PAIR_240_TOL = 0.25`. Sum their signed watts. Other deltas remain separate.
  This is the detector's 240 V heuristic. Simultaneous balanced steps on both
  legs were also [observed on hardware](findings.md).
- Merge positive steps on exactly the same legs when each next stage is within
  `STAGE_FRAMES = 120` frames of the previous stage. At `FPS = 60`, that is 2 s.
  The gap is measured from the last stage, so a chain can span more than 2 s.
  Add watts and record each stage as `(frame offset from first stage, watts)`.
  The first stage has offset zero. Steps on other legs do not interrupt merging.
  A negative step on the same legs does. Negative steps never merge.

## Steps to runs

Each positive step starts a run. Each negative step closes at most one open run:
the most recent compatible on-step on exactly the same legs. The size test is
`abs(on_w - abs(off_w)) <= max(0.15 * on_w, 20 W)`
(`PAIR_TOL = 0.15`, `PAIR_ABS_W = 20`). An unmatched off is discarded.

When readings are available, sustained power below the on-size minus that tolerance
retires the on as `unresolved` before matching an off. A completed newer compatible
cycle also retires older candidates if power during their supposed overlap cannot
support both loads. Evidence uses maxima in fully covered five-second bins, skips
startup/stages and off transitions, and stays within a frame-counter generation.
Missing data and isolated dips do not reject runs. For `both`, use total leg power.
No off time is invented; retired runs contribute zero duration/energy and cannot
be revived by pattern timing. Supported long runs retain the six-hour pairing limit.

`MAX_RUN_S = 6 * 3600` (6 hours) limits pairing. Before considering an off, the
detector removes older ons from the pairing candidates. Unclosed runs older than
6 hours at recompute time are dropped from the result, not retained as unresolved.
An off at exactly 6 hours can still pair.

Detection is separate for each frame-counter `generation`. A run cannot close
across a monitor reboot. Closed duration is `(end_frame - start_frame) / 60`.
Expiry and open-run age use timestamps instead.

After pattern assignment, `resolve_open_runs` sets the remaining open runs to:

- `running` while age is at most `max(30 s, 3 * median closed duration)` for that
  pattern in the current window. With no closed examples, the limit is 600 s.
- `unresolved` after that limit. The off may have been missed. Duration and energy
  are reported as zero. This is a heuristic status, not proof the device is off.

For even-sized lists, the code uses the upper middle value as its median.
Running duration uses elapsed timestamp time. Run energy is estimated as
`on_w * duration_s / 3600` Wh. It assumes constant power, including during staged
startup. Today's energy clips the interval at local midnight and excludes unresolved
runs. These are estimates, not appliance energy measurements.

## Runs to persistent patterns

`WINDOW_S = 48 * 3600`: each recompute reloads the last 48 hours of deltas.
It rebuilds runs, assigns patterns, resolves open runs, then computes naming hints.
Runs are recomputed; pattern IDs and names persist in `settings.db`.

A run can join a pattern only when its legs and stage category match. The category
is `min(stage count, 2)`: one stage versus two or more. Stage timing and individual
stage sizes are not clustering features. Among eligible patterns, choose the
nearest centre satisfying `abs(run_w - center_w) / center_w <= CLUSTER_TOL = 0.12`.
If none fits, create a pattern at the run's size.

Assignment uses the existing centres; centres are refreshed after assignment.
An unnamed centre becomes the upper median on-size of its assigned runs in this
window. A named centre stays frozen. The SQL update also guards against a name
being added while recomputation is in progress. Clearing a name permits later
centre updates again. Names are trimmed to 60 characters; blank becomes null.
Naming also clears the ignored flag.

The stored count `n` is this window's run count, not a lifetime count. `last_seen`
is the latest assigned run's start timestamp. Pruning deletes only patterns with
no name, `ignored=0`, `n=0`, and `last_seen` (or creation time) older than
`PRUNE_AFTER_S = 7 * 86400` (7 days). Named and ignored patterns survive pruning.
Ignoring does not remove runs from detection. Named patterns remain in the load
list even when absent from the window; other absent patterns are omitted.

## Sense naming hints

`load_sense_ons` reads `device_power` in the same 48-hour window. It excludes the
exact names `Always On` and `Other`. For each device, crossing 20 W from below counts
as one on, so Sense ramping its estimate (203 → 298 → 1700 W) or creeping up
(0 → 15 → 100 W) is a single turn-on. The initial previous value is zero, so the first
record in the window can count as an on even if the device was already running.
The hint size is the device's peak from that row until it drops below 20 W again,
at most 30 s later, so a following activation is not absorbed.

`sense_hints` compares each run with Sense ons from 10 s before to 30 s after its
start, inclusive. Sizes must differ by at most 40% of the larger size. Runs are
processed in time order and each Sense on corroborates at most one run (the nearest
unused one), so a single Sense activation cannot produce two matches. The most frequent name becomes a suggestion only
with at least 2 matches and a share of at least 0.4 of all runs for that pattern.
It returns `name`, `matches` and `share`; it never applies a name automatically.
These are weak correlations, not verified appliance labels. A database error while
loading hints leaves the hints empty without discarding detection results.

## Worked example: microwave

The [labeled 30 s sample](findings.md#2026-10-15-microwave-signature-labeled-ground-truth)
contains these verified L2 deltas:

| Start frame | Delta | Observed role |
|---|---|---|
| 25796520 | +299 W | Fan, light, turntable |
| 25796610 | +1726 W | Magnetron |
| 25798290 | −1907 W | Off |

Applying the code's rules to these three deltas gives the following worked result.
Both ons exceed 30 W. Their 90-frame gap is 1.5 s, within 120 frames. They merge
to 2025 W on L2 with stages `[(0, 299), (90, 1726)]`. The off differs by 118 W,
within `max(303.75, 20)` W, so it closes the run. Duration is 1770 / 60 = 29.5 s.
Estimated energy is 16.59 Wh. With no matching existing centre, this would create
a 2025 W, L2, multi-stage pattern. This calculation is derived from the code;
the pattern ID and assignment depend on the other runs and saved patterns.

## Known limitations

- Multi-step offs do not combine. A load whose total off arrives as several
  smaller drops may never close. It can become unresolved and later expire.
- One appliance can split across patterns when its on-size changes by more than
  the clustering tolerance, or its startup appears as one stage on some runs and
  several on others. Patterns can share a user name; the Loads view groups them into one card (stats
combined, rename applies to all) and the inbox suggests existing names of similar
size on the same legs, but detection itself keeps them separate.
- Inferred failure modes: unrelated ons close together on the same legs can merge;
  coincident balanced loads can look like a 240 V load; similar loads can share a
  pattern or pair with each other's offs. The rules do not establish identity.
- Steps below 30 W, missed deltas, window boundaries and counter resets can hide
  or truncate runs. The timing conversion assumes 60 Hz. Open-run status and
  naming hints need representative examples before they are useful.
