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

Each positive step starts a run. Each negative step first tries one open run:
the most recent compatible on-step on exactly the same legs. The size test is
`abs(on_w - abs(off_w)) <= max(0.15 * on_w, 20 W)`
(`PAIR_TOL = 0.15`, `PAIR_ABS_W = 20`). Runs with `inrush_w` set instead allow
`max(0.20 * on_w, 20 W)` because settled sizing is an estimate. A single compatible, power-supported
run always wins over a pair.

If there is no single match, try pairs among the 12 most recent open runs on
exactly those legs, each started within `MAX_RUN_S`, and whose starts are at most
`SHARED_START_S = 600 s` apart (using frame counters). Compare the summed run
watts to the off with the same `max(0.15 * sum_w, 20 W)` tolerance. Power must
support each run throughout its own duration and their sum during their overlap,
using the sustained-power evidence rules below. Prefer the smallest gap between
start frames, then the smallest absolute watt error; remaining ties favor recent
starts. Both runs end at that off and serialize `closed_by: "shared-off"`, meaning
"switched off together"; otherwise `closed_by` is null. Each retains the full
observed `off_w`, not an apportioned share. Energy uses each run's own watts and
duration. Rejected duration outliers clear `closed_by` along with their end.

The existing size buckets serve single matching; a separate ordered open index
makes the pair fallback examine at most 66 pairs per off, without scanning all
unresolved starts. `SHARED_OFF_CAP = 0` disables the fallback for falsification.
An unmatched off is discarded. A long-running load outside the most recent 12
cannot close through this fallback, even if an older pair could explain the off.
This is a bounded heuristic, not proof of appliance identity. Shared closures
remain weak individual-load evidence in cycle detection: they do not establish
clean repeated closures or chatter, or remove program fragments from envelopes.

Before pairing, an on-step of at least 300 W can be sized by its settled power
rise instead of the monitor's reported startup surge:

- Baseline: mean on the step's legs from 12 s to 2 s before its first stage.
- Settled: mean from 6 s to 20 s after its last stage; for `both`, use L1 + L2.
- Require at least 70% covered time in each window. A covered one-second bin
  needs finite readings on the relevant legs; missing/invalid bins add no coverage.
  Bin means are weighted by their overlap with the window, with no gap filling.
- Skip when another step of absolute size at least 120 W occurs after the first
  stage through 20 s after the last stage, inclusive at the far end, on exactly
  the same leg category (`L1`, `L2`, or `both`). The candidate's own merged stages
  are excluded. An event on a different leg category does not trigger this guard;
  it can still affect a `both` window's measured rise.
- If `30 W <= settled - baseline < 0.8 * reported_on_w`, set `run.w` to that rise
  and `run.inrush_w` to the reported size. Otherwise retain the reported size and
  leave `inrush_w` null. Preserve the reported `stages` in either case.

Pairing, sustained-power checks, pattern clustering and estimated energy all use
the adjusted `w`. Plain runs keep 15% pairing tolerance; surge-sized runs allow
20%. The six-hour limit still applies.
`Detector.recompute` loads one-second baseline means in SQL for every on, plus settled means for eligible starts,
coalescing overlapping windows and using the `(generation, frame)` index. The
five-second maxima used below and cycle energy bins remain unchanged. Raw-reading
replays/tests compute the same one-second means in Python. Supplying only coarse
five-second bins without startup means skips adjustment; delta-only databases and
missing startup readings also retain the original size.

When a covered pre-start mean is available (the same first-stage −12 s to −2 s
window used for sizing), sustained power below `pre + 0.85 * run.w - 300 W`
retires the on as `unresolved` before matching an off. The 300 W slack allows
other background loads to switch off. Without a covered baseline, retain the
absolute `run.w - max(0.15 * run.w, 20 W)` floor, including for surge-sized runs.
A completed newer compatible
cycle also retires older candidates if power during their supposed overlap cannot
support both loads. Shared-off matching checks each run and their combined overlap;
combined checks use the earlier start's baseline and the later start's interval,
so the older load is counted once. Evidence uses maxima in fully covered five-second bins, skips
startup/stages and off transitions, and stays within a frame-counter generation.
Missing data and isolated dips do not reject runs. For `both`, use total leg power.
No off time is invented; retired runs contribute zero duration/energy and cannot
be revived by pattern timing. Supported long runs retain the six-hour pairing limit.

`MAX_RUN_S = 6 * 3600` (6 hours) limits pairing. Before considering an off, the
detector removes older ons from the pairing candidates. Unclosed runs older than
6 hours at recompute time are dropped by standalone `detect_runs` by default.
`Detector.recompute` retains them as unresolved for the full 48-hour window so
yesterday's [program cycles](cycles.md) can still be detected. This does not extend
pairing or count unresolved energy. An off at exactly 6 hours can still pair.

Detection is separate for each frame-counter `generation`. A run cannot close
across a monitor reboot. Closed duration is `(end_frame - start_frame) / 60`.
Expiry and open-run age use timestamps instead.

After pattern assignment, `resolve_closed_runs` rejects extreme duration outliers
in unnamed patterns with at least eight closed runs. For each candidate, exclude
that run from the reference durations; require its duration to exceed **both**
20 times the peers' upper median and four times their nearest-rank 95th percentile.
Clear the candidate's end frame, end timestamp and off watts, and mark it
`unresolved`, contributing zero duration/energy. Repeat until no more closures
are rejected, so rejected outliers cannot keep inflating the reference tail.
Unassigned runs and named patterns are exempt. No absolute duration ceiling is
added: patterns that normally run for tens of minutes retain their long runs.
Rejected offs are not reassigned; they remain ambiguous.

Then `resolve_open_runs` sets the remaining open runs to:

- `running` while age is at most `max(30 s, 3 * median closed duration)` for that
  pattern in the current window. With no closed examples, the limit is 600 s.
- `unresolved` after that limit. The off may have been missed. Duration and energy
  are reported as zero. This is a heuristic status, not proof the device is off.

For even-sized lists, the code uses the upper middle value as its median.
Running duration uses elapsed timestamp time. Run energy is estimated as
`run.w * duration_s / 3600` Wh. It assumes constant power, including during staged
startup. Today's energy clips the interval at local midnight and excludes unresolved
runs. These are estimates, not appliance energy measurements.

## Runs to persistent patterns

`WINDOW_S = 48 * 3600`: each recompute reloads the last 48 hours of deltas.
It rebuilds runs, assigns patterns, rejects closed duration outliers, resolves
open runs, rebuilds [cycles and cycle membership](cycles.md), then computes naming hints.
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

Patterns store `inrush_w` as the median reported surge size among adjusted members
in this window (the average of the two middle values for an even count), or null
when there are none. Non-surge members do not enter this median. This field is
refreshed even for named patterns, whose running-size centre stays frozen. Existing
settings databases gain the nullable column automatically without changing names.

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

- Settled sizing is a background-subtracted heuristic, not proof of a motor or
  device identity. Unreported background changes and small switching below the
  overlap threshold can distort the rise, including creating a false small run
  from a surge-shaped delta with no sustained load. Baseline-relative floors and
  20% surge pairing recover the 901 W pool replay against its 1054 W off; see the
  [round 3 findings](findings.md#2026-10-16-baseline-relative-evidence-replay-round-3).
  The 300 W allowance makes small-load evidence weak, and shared closures during
  program chatter can still be questionable even with nearby starts.
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
- Duration rejection is statistical evidence, not proof of a missed off. Sparse
  samples, several outliers inflating the upper tail, or shared patterns with
  different operating modes can leave long mis-pairs intact. Named patterns are
  deliberately exempt from this heuristic.
