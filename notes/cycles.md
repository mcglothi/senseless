# Program activity cycles

Backend implementation: [`senseless/cycles.py`](../senseless/cycles.py), rebuilt
with runs over the detector's 48-hour window. Cycle patterns and names live in
`settings.db`; no writes go to the collector database. The [API](senseless-api.md)
exposes envelopes, names, membership, fragment flags and a named count metric.
Frontend support is separate work; this change does not hide anything in the UI.

## Detection decisions

- Use merged on-steps of at least 120 W, separately for L1, L2 and both, within
  one monitor generation. Exclude named run patterns and Hunt-style chatter:
  single-leg patterns with at least 40 window runs, an upper-median **closed**
  duration at most 600 s and centre at most 300 W. A pattern with no known
  closures cannot qualify as chatter based on fabricated zero durations.
- Exclude closed runs from gap bridging when their pattern has at least three
  closures and at least 80% of its window runs are closed. This removes repeated
  clean switching appliances before building bursts. Individual closures in
  mixed program patterns remain candidate evidence: removing every closed run
  splits the verified wash at its pause. This reliability rule is a heuristic.
- Consecutive candidate gaps must be at most **240 s**. The proposed 180 s split
  the replayed washer at a 210 s pause. Keep at least 12 steps, 5 minutes through
  4 hours, and at least 0.15 steps/minute. At least half the candidates must lack
  reliable closed runs; this extra guard rejects clean switching appliances.
- Require five-second power bins with the existing coverage rule (samples within
  one second of each edge, no gap over two seconds). At least 80% of both the
  two-minute baseline and burst must have finite power on the relevant legs.
  The burst mean must exceed baseline by at least 100 W. Missing evidence fails
  confirmation. Power evidence never crosses a generation.
- SQL now returns bin means as well as the existing maxima. Baseline is the
  covered time-weighted mean immediately before the first candidate. Integrate
  signed mean power minus baseline over covered burst seconds; do not fill gaps
  or clip negative deviations. Peak uses bin maxima minus baseline. Partial bins
  contribute proportionally. For `both`, use summed leg power.
- Start/end are the first/last candidate on timestamps; no physical off is
  invented. `has_heat` means any merged positive step at least 1000 W on the same
  legs inside the envelope, including steps excluded from candidate selection.
- Member runs are the **unclosed supporting candidates** in the confirmed burst.
  Closed independent appliances, named/background patterns and steps below
  120 W do not become members merely by starting inside the envelope.
  A pattern is `part_of_cycles` when at least 70% of its window runs are members.
  Ordinary run statistics remain available and overlap cycle estimates; do not
  add the two energy totals together as independent device energy.
- The detector retains expired missed-off starts as unresolved for 48 hours
  **only for cycle detection**, so yesterday's program is not lost. Run APIs,
  hints, pattern assignment/counts/centres and load statistics use the original
  six-hour expiry. Cycle-only shapes get temporary IDs without writing run
  patterns. Public run status resolution is independent of the expanded list.

Cycle matching requires identical legs and relative differences at most 35%
in duration and 40% in energy and median step size. Choose the smallest sum of
squared differences normalized by those tolerances; ties choose the lower ID.
Step rate is stored but is not a matching gate. A cycle remains provisional until
its latest candidate is more than 240 s old; it has a separate pattern while
growing and cannot match or move completed centres. Persist cycle-ID assignments
in `settings.db`, preserving names and ignores through growth and restarts.
On completion, anonymous provisional cycles can join completed clusters; saved
ones keep their assignment. Their centres mature with the envelope and named
centres freeze after completion. Refresh other unnamed centres from completed
window medians. Counts are recomputed, not added
on every refresh. Prune absent unnamed, unignored patterns after 14 days; named
and ignored patterns persist. Cycle IDs derive from generation, legs and the
first candidate frame, and can change if the burst start leaves the window.

## Replay verified against local capture, 2026-10-16

Loaded the scratch JSON directly into a temporary SQLite database **outside the
repository**, then called `Detector.recompute` at the capture's last reading
(13:30:49 America/New_York). Inputs: 3,898 deltas (05:31–13:30) and 41,862
readings (07:31–13:30); generation zero, fresh pattern settings. The temporary
database was deleted after replay. No captures or account identifiers are stored
in this repository.

| Result | Value |
|---|---|
| Confirmed envelopes | **One, L1, 08:19:38–09:06:00** |
| Duration / candidate steps | 46 min 22 s / **120** |
| Candidate rate | 2.588 steps/min |
| Baseline-subtracted energy | **219.18 Wh** |
| Peak above baseline / heater step | 1540.05 W / no |
| Supporting member runs | 109, all unclosed |
| Fragment patterns flagged | 13 of 54 public window patterns |
| Public runs / expanded cycle evidence | 1,369 / 1,465 |
| L2 / both-leg cycles | **Zero / zero** |

The labeled washer window was approximately 08:21–09:06. The activity envelope
includes an earlier qualifying step at 08:19:38; the gap heuristic cannot prove
that step was part of the wash. The estimate measures excess whole-leg power,
not isolated washer consumption. Other loads and a changing baseline can bias it.

The late dryer window contains clean both-leg runs around 5,982 W and 5,601 W,
both closed and outside any cycle. Late single-leg high-power fragments also
remain outside cycles. No background-only cycles were emitted. Recomputing
after naming the cycle pattern `Washer` kept the same cycle ID, one pattern,
and its name, including a detector restart. Public run dictionaries (apart from
cycle membership) and persisted pattern IDs, counts and centres exactly match
the pre-cycle expiry pipeline on these inputs. These observations verify this replay, not general accuracy for
other household programs.

## Synthetic validation and limits

`tests/test_cycles.py` generates fixtures in code: unresolved washer activity,
a dishwasher-style 1200 W stage, each leg configuration, power evidence failures,
clean switching loads, pauses, monitor generations, two days of washes,
clustering, naming/restart/centre freezing/pruning, membership and API/metrics.
The chatter regression includes enough missed offs to pass the unpaired guard:
normal filtering yields zero cycles; replacing chatter exclusion with an empty
set yields one false cycle. This falsifies the exclusion independently of the
clean-load guard.

Not yet verified against real traffic: dishwasher behavior, concurrent programs,
true start/end precision, heater attribution and detection across long program
pauses. Fully paired programs can be missed by the unpaired guard. A missing
baseline or less than 80% coverage rejects a candidate; an ongoing burst ends at
its latest observed step. At midnight, today's
energy is apportioned by envelope duration rather than re-integrated by time.

Validation run (2026-10-16):

```sh
UV_CACHE_DIR=/private/tmp/senseless-uv-cache UV_NO_SYNC=1 uv run python -m pytest -q
```

Result after review fixes: **364 passed, 12 failed**. All 12 failures are sandbox socket-bind
`PermissionError: [Errno 1] Operation not permitted`; none reached an HTTP
assertion. The cycle suite has **33 passing tests**, including real route
handlers and write middleware exercised without network sockets. The API index
coverage check also passes. The cache override and `UV_NO_SYNC=1` use existing
local test dependencies because the sandbox blocks the default uv cache and
network downloads. `git diff --check` and the AIKB v2 readiness trial pass.

Each review fix was independently reverted while retaining its regression tests:

| Reverted fix | Regression | Result |
|---|---|---|
| Saved cycle assignment | `test_growing_cycle_keeps_saved_assignment_across_restart` | Both name/ignore cases fail |
| Reliable closures bridging gaps | `test_clean_closures_cannot_bridge_separate_programs` | Two programs merge; test fails |
| Overlap-only membership | `test_membership_preserves_independent_and_excluded_loads` | All four clean/named/small/chatter cases fail |
| Expired evidence in public runs | `test_expired_cycle_evidence_does_not_change_standalone_runs` | 124 runs instead of 2; test fails |

Restoring the fixes returns all 33 cycle tests to passing. Fixtures contain only
synthetic data; scratch capture data and temporary replay databases stay outside
the repository. Existing clustering fixtures now use distinct cycle IDs for
distinct programs and wait for completion before testing feature gates.

Socket-blocked tests:

| File | Test |
|---|---|
| `tests/test_api.py` | `test_stream_delivers_snapshot_then_events` |
| `tests/test_api.py` | `test_shutdown_is_prompt_with_an_open_stream` |
| `tests/test_api.py` | `test_series_and_deltas_accept_windows` |
| `tests/test_api.py` | `test_circuits_api` |
| `tests/test_billing.py` | `test_billing_api` |
| `tests/test_billing.py` | `test_today_cost_uses_the_tariff` |
| `tests/test_detect.py` | `test_loads_api_name_and_ignore` |
| `tests/test_floorplans.py` | `test_http_api` |
| `tests/test_floorplans.py` | `test_http_write_security_and_upload_limits` |
| `tests/test_history.py` | `test_history_endpoint` |
| `tests/test_hunt.py` | `test_hunt_api_long_poll_and_label_to_room` |
| `tests/test_hunt.py` | `test_stream_snapshot_carries_hunt` |
