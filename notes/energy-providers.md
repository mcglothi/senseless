# Provider backend and contributions

Implemented 2026-10-16 for the 0.10.0 backend. The contract is
[energy-providers-spec.md](energy-providers-spec.md); HTTP details are in
[senseless-api.md](senseless-api.md#billing). This change does not publish a release or modify the UI.

`billing.Billing` owns settings, cycles, bills, and insights. `energy.usage.UsageStore` owns interval
persistence and hourly aggregation; `energy.importers` owns content detection/normalization;
`energy.tariff` validates packs and prices usage. Built-in prices live only in JSON. There is no
runtime discovery, code execution, external plugin loading, or automatic tariff fetching.

## Storage, migration and compatibility

Everything uses `settings.db`, separate from the collector database:

- `usage_interval(ts, seconds, kwh, temp_f, received, source)` upserts on `(ts, seconds)`.
  Initialization copies old `meter_hourly` rows as 3600-second `avangrid-csv` observations with
  `INSERT OR IGNORE`. The old table is retained for rollback/reference; it is no longer written or
  read by analytics. Reopening cannot overwrite a corrected interval with stale legacy data.
- Hourly aggregation splits at observation boundaries, selects the shortest covering interval, and
  sums proportional energy. Coarser observations fill gaps. Temperature is weighted by covered time;
  completeness uses covered seconds, including 23/25-hour local DST days. This assumes uniform energy
  within a coarse interval. Overlapping observations of equal length choose the latest start.
- Old `cmp-a`, `cmp-a-tou`, `cmp-et` settings become `rate-a`, `a-tou`, `electric-technology` in pack
  `us-me-cmp`, with supply `standard-offer-2026`. Legacy IDs still work in settings writes and explicit
  estimates; an explicit legacy estimate returns the requested plan ID. Default/selected plan IDs and
  comparison keys are canonical. Existing response keys and upload kinds `meter-csv` / `bill-pdf`
  remain; imports add `importer` and `label`, estimates add `lines`.
- Existing custom rates become a component-schema user plan in `user_tariffs`; the old custom settings
  remain an editing compatibility view. Complete user packs live in `tariff_packs`. Built-in IDs are
  read-only, and selected user packs cannot be deleted or replaced with a missing selection.
- Existing bill columns remain, with normalized `lines_json` added. Old CMP import rows receive
  normalized lines and `source=bill-cmp`; manual legacy bills retain their original amounts/periods.
  The CMP plugin checks historical bills using their own captured rates, independent of today's pack.
  Generic checks require the period to fall within selected plan and supply effective dates. CMP
  calibration still requires matching captured fixed/delivery rates. Generic manual entries snapshot
  tariff components, supply and taxes; only matching snapshots within valid effective dates calibrate.

Pack components cover fixed monthly/daily charges, allowances, flat energy, tiers, TOU, usage credits,
minimum charges and riders. Bundled plans ignore supply. Tiers/credits/minimums and monthly allowances
scale with `months`; daily fixed charges use actual calendar days in bill/cycle/month analysis and explicit `days` in the
Python engine (default `months * 30.44`). Minimums apply
after the plan's credits. Taxes apply after delivery/supply; the CMP pack preserves the exemption's
per-kWh tax base and existing rounding. Marginal rates use differences of unrounded estimates. No
intermediate currency rounding is imposed on estimates; returned lines and totals round to cents.

TOU profiles contain `{ts, kwh, holiday?}` with energy summing to the requested kWh. Pricing uses the
pack timezone, month/hour/weekday filters and explicit holiday flags. There is no jurisdiction-specific
holiday calendar; CMP retains the previous holiday approximation. A scalar `on_peak_kwh` works for one
TOU period; multiple periods need the Python engine's hourly profile. Annual comparisons use actual
hourly profiles for each plan; cycle comparisons scale recent hourly observations. These comparisons
use current tariff data across the window, not historical tariff versions. Expiry metadata is exposed;
a selected expired plan is not silently replaced.

## Adding a tariff pack

1. Add a JSON file to `senseless/energy/packs/`, using `us-me-cmp.json` as a schema example. Include a
   stable ID, utility, region, currency, IANA timezone, source, effective/expiry dates, delivery or
   bundled plans, separate supply options where appropriate, and taxes. Cite the applicable tariff
   source in metadata. Never put account/customer-specific information in a pack.
2. Keep prices as data: use supported charge components; don't add utility conditionals to Billing.
   For new charge shapes, extend the generic validator/engine with a precise arithmetic test first.
3. Add synthetic regression examples for thresholds, exemption boundaries, TOU periods and totals.
   `validate_pack` rejects unknown fields/components, invalid dates/timezones/hours and negative rates
   (negative amounts are allowed only for credits). Run `uv run pytest -q`.

A user can POST the same schema to `/api/billing/tariffs`, then select its pack/plan/supply in settings.
Plans may remain selected outside their effective dates for comparison; bill checks respect those dates.

## Adding an importer

1. Add an in-repo module in `senseless/energy/importers/` with `NAME`, `LABEL`, `detect(bytes) -> float`
   and `parse(bytes) -> ImportResult`. Register it explicitly in `IMPORTERS`. Detection must inspect
   content, never filenames/MIME types, and the winning confidence must exceed 0.5.
2. Return usage tuples `(ts, seconds, kwh, temp_f, received)` with `kind=usage` and `source=NAME`, or a
   normalized `kind=bill` object. A parser may implement `check(bill)` using captured rates. Raw input
   is transient; whitelist dates, usage, temperature, rates and amounts immediately. Use constant
   `BillingError` messages; suppress parser diagnostics that could quote input. Never retain identity
   columns, raw bytes, extracted text, meter readings or identifiers in results, storage or logs.
3. Add fake-only samples and assertions against database dumps, preview/import responses, errors and
   logs. Include malformed data and atomic rejection tests. Verify preview does not write anything.
   Test the privacy assertion by deliberately inserting a fake identity sentinel into a temporary DB
   and checking that it fails. Never use a real export as a committed fixture.

CSV extensions normally need a header profile in `csv_generic.py`, or no code at all: users can save
column mappings via `/api/billing/csv-profiles`. Only mapped usage/time/temperature values are retained.
Names are short profile slugs; mapped identity-looking columns are rejected. All headers match without
case/outer whitespace. Unknown preview header labels are conservatively redacted to avoid exposing
household information embedded in headers.

**Unverified against real exports:** Smart Meter Texas (15-minute, Central time, separate surplus
flow) and PG&E-style Green Button CSV. Synthetic tests exercise their documented header shapes,
midnight, Wh conversion and surplus. Ambiguous/nonexistent local DST timestamps are rejected; an
explicit-offset timestamp/mapping can disambiguate those rows. Avangrid retains offset-aware hours.
Green Button XML resolves Atom links to ReadingTypes, handles Wh multipliers/received flow and sums
multiple UsagePoints/blocks into the normalized household series; no UsagePoint IDs survive parsing.
