# Appliance cycles: washers, dishwashers and other programs (spec)

Status: backend implemented (2026-10-16); see [cycles.md](cycles.md) for decisions, validation and limits.
Frontend integration remains separate. The step detector (senseless/detect.py) pairs on/off steps into runs and clusters runs into
patterns. That works for things that switch cleanly (a kettle, a dryer element: the Dryer pattern is a clean
~5.6-6 kW 240 V on/off). It fails for appliances that run a *program*: a washer's motor agitates and spins,
producing a step every ~20 s for 45 minutes, ramping down softly instead of switching off. Real data
(2026-10-16, 08:21-09:06, L1): ~120 on-steps of 150-900 W, nearly all "unresolved" (no matching off), spread
across ~20 patterns (ids 20, 30, 31, 41-53, 55, 58-60). Naming any one pattern labels a slice of the washer,
and the Loads inbox fills with fragments.

## Goal
Detect *cycles*: bursts of program activity on the same leg(s), as one event with a start, an end, energy,
and a fingerprint; cluster similar cycles into cycle patterns; let the user name them ("Washer",
"Dishwasher"); stop the fragments cluttering the inbox.

## Detection (per generation, over the detector's 48 h window, recomputed with runs)
1. Candidate steps: on-steps (after to_steps/merge_stages) of >= 120 W on L1, L2 or both, EXCLUDING steps
   that belong to frequent short "chatter" patterns (same rule as hunt.py's background filter: >= 40 runs
   in 48 h, median <= 10 min, <= 300 W) and steps of named non-cycle patterns.
2. Burst: group candidate steps on the same legs where consecutive gaps <= GAP_S (start 180 s). Keep
   bursts with >= MIN_STEPS (start 12) steps, duration 5 min .. 4 h, and a step rate >= 0.15/min.
3. Confirm with power: from readings (binned like PowerEvidence, 5 s bins via SQL), the burst's legs must
   average >= 100 W above the 2-minute pre-burst baseline over the burst. This rejects coincidences.
4. Cycle record: {start_ts, end_ts, legs, steps, step_rate, median_step_w, p90_step_w, energy_wh (leg
   power above the pre-burst baseline integrated over the burst, from readings), peak_w (above baseline),
   has_heat (any step >= 1000 W inside: dishwasher/washer heater), member run keys}.
5. Runs whose start falls inside a cycle get cycle=<cycle id>; patterns where >= 70% of their runs in the
   window are cycle members are flagged part_of_cycles=true (the Loads inbox hides them; they stay
   nameable from an expanded view).

## Cycle patterns (settings.db, like patterns)
Table cycle_patterns(id, legs, duration_s, energy_wh, step_rate, median_step_w, n, name, ignored, created,
named_at, last_seen). Assign each cycle to the nearest pattern with the same legs and within: duration
±35%, energy ±40%, median step ±40% (normalised distance, nearest wins); else create one. Named patterns'
centres stay put (same rule as Patterns). Prune unnamed ones unseen for 14 days.

## API
- GET /api/cycles?minutes= — cycles overlapping the window (as above + pattern id + name).
- GET /api/cycle-patterns — patterns with today's/48 h stats (count, kWh, last seen, median duration).
- POST /api/cycle-patterns/{id}/name {name|null}, POST /api/cycle-patterns/{id}/ignore {ignored}.
- /api/loads patterns gain part_of_cycles; /api/runs rows gain cycle (id|null).
- Add all routes to API_INDEX (tests enforce it) and document them in notes/senseless-api.md.
- /metrics: senseless_cycles_today{name} gauge (named patterns).

## Real data for development (local scratch, NEVER copy into the repo)
/private/tmp/claude-501/-Users-mcglothi-code/cf41385a-ef55-4720-a61b-1f3ac8c8a46f/scratchpad/cycles/
  deltas-today.json  (05:31-13:30: the washer 08:21-09:06 on L1, a dryer 13:20- on both legs + L2)
  readings-6h.json   (07:31-13:30 per-second readings)
Replay them through the detector: the washer must come out as ONE cycle on L1 spanning ~08:21-09:06 with
~120 steps; the dryer element (clean 240 V on/off) must NOT become a cycle (its runs pair); background
chatter must not form cycles. Report what you get.

## Tests
Synthetic fixtures generated in code (no real data): a washer-like burst (steps every 20 s, 150-900 W,
unresolved offs, readings elevated), a dishwasher-like burst with a 1,200 W heater stage, chatter that must
not cycle, a clean appliance that must not cycle, two washes on different days clustering into one
pattern, naming persistence, inbox flagging. Falsify the chatter exclusion by removing it.
