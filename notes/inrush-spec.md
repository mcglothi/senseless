# Settled-level run sizing for motor loads (spec)

Status: planned. Motors (pumps, compressors, fans) draw a start-up surge. The monitor reports the on-step at
or near that surge, so detect.py sizes the run by the surge and its off-step (the running level) never pairs:
the run stays "unresolved", has no end, no runtime and no energy.

Real cases (2026-10-16, Senseless prod; data below):
- Pool pump + salt cell on L2 (one timer): on-steps 08:30:26 L2 +1239 (no level change), 08:31:00 L2 +2230
  (L2 settled ~+940 W: 983 -> 1923 W averaged 08:31:05-08:31:50), 08:31:55 L2 +476 (salt cell; L2 ~2204 by
  08:32-08:34). Off: 14:28:10 L2 -1054 (2100 -> 1068 W). Expected after the fix: one run (or the pump run plus
  a small salt-cell run) from 08:31 to 14:28 sized ~0.94-1.2 kW, closed by the -1054 off; today's energy ~6 kWh.
- Probable well pump, 240 V: 13:13:12 L1 +839 L2 +867 (both 1706); 13:15:33 L1 -398 L2 -367 (both -765).
  Expected: a run of ~0.75-0.85 kW (settled), closed at 13:15:33.

## Rule
For every on-step (after to_steps/merge_stages, >= 300 W) where readings are available:
- pre = mean leg power (the step's legs; both = L1+L2) over [t-12 s, t-2 s]
- settled = mean over [t+6 s, t+20 s] (after the last stage of a multi-stage step)
- rise = settled - pre
- If rise >= MIN_STEP_W and rise < 0.8 x step.w, the step is a surge: size the run by rise (run.w = rise) and
  keep the reported size as run.inrush_w. Otherwise leave it as is.
- Skip the adjustment (keep the step size) when another step >= 120 W on the same legs falls inside
  (t, t+20 s] (overlapping switching makes the windows ambiguous), or readings cover < 70% of either window.
- Patterns: cluster by the adjusted size (the running level), so a pump's runs share a pattern regardless of
  surge size; keep median inrush in the pattern/loads output (inrush_w) for display.
- Pairing uses the adjusted size, so the off-step (running level) closes the run.
- Compute the windows from SQL-binned readings where possible (1-2 s means, or the existing bins if fine
  enough) - recompute must stay well under 2 s on 48 h of data (benchmark below).

## Output
Run.as_dict gains inrush_w (null when no surge). /api/loads patterns gain inrush_w (median of members, null if
none). Document in notes/detection.md + notes/senseless-api.md.

## Data (local scratch only, NEVER into the repo)
/private/tmp/claude-501/-Users-mcglothi-code/cf41385a-ef55-4720-a61b-1f3ac8c8a46f/scratchpad/inrush/
  deltas.json   (prod /api/deltas, 02:30-14:33)
  readings.json (prod per-second readings, 08:03-14:33)
Replay through Detector and report the pool pump and well pump runs before/after, plus counts of runs
adjusted, and confirm the Dryer element (clean 240 V ~5.6-6 kW on/off) and the washer cycle (08:19-09:06, L1)
are unchanged.

## Tests
Synthetic, in tests/: a surge motor (step 2200, settles 1000, off -1050) closes as one ~1000 W run with
inrush 2200; a clean resistive load is untouched; overlapping steps inside the settle window skip the
adjustment; a 240 V surge; missing readings fall back. Falsify by removing the adjustment.
Benchmark: python scratchpad/perf/bench.py (48 h, ~334k readings): report recompute time.
