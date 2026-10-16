# Utilities, tariffs and imports: a provider layout (spec)

Status: backend implemented for 0.10.0 (2026-10-16); UI integration is separate. Replaces the CMP-only
implementation described in billing-import-spec.md. Synthetic tests cover the provider layout;
Smart Meter Texas and Green Button CSV remain unverified against real exports.

Implementation/contribution notes: [energy-providers.md](energy-providers.md).

## Three layers

| Layer | Generic core | Per-utility part |
|---|---|---|
| Usage | one interval store (any interval: 15 min, 60 min) | importers: Green Button XML, Green Button CSV variants, Smart Meter Texas CSV, a saved column mapping for anything else |
| Pricing | one tariff engine that understands all common tariff shapes | tariffs as data files ("tariff packs"), never code |
| Bills | one normalized bill + checks/calibration | bill parsers as in-repo plugins (CMP first) |

Plugins live in the repo and arrive by pull request; nothing is loaded from outside at runtime (this app
holds household data). Tariff packs are data only.

## Package layout (Python)

    senseless/energy/
      __init__.py
      usage.py          # interval store in settings.db, aggregation helpers
      tariff.py         # tariff schema validation + the engine (estimate a bill for a usage profile)
      packs/            # built-in tariff packs (JSON), one file per utility
        us-me-cmp.json
      importers/
        __init__.py     # registry: detect(data) -> best importer; import_data(data) -> result
        greenbutton_xml.py
        csv_generic.py  # header-profile CSVs (Avangrid/CMP, Smart Meter Texas, Green Button CSV) + saved mappings
        bill_cmp.py     # CMP bill PDF/text parser (move parse_cmp_bill_text here)

billing.py keeps the Billing class (settings, cycle, bills, insights) but delegates parsing, storage and
pricing to senseless/energy. Keep public behaviour and existing API responses; tests must keep passing
(adapt tests that reach into moved internals).

## Usage store

Table usage_interval(ts INTEGER NOT NULL, seconds INTEGER NOT NULL, kwh REAL NOT NULL, temp_f REAL,
received REAL, source TEXT NOT NULL, PRIMARY KEY (ts, seconds)) — ts = interval start (unix seconds),
seconds = 900 or 3600 (accept 300..86400). Migrate existing meter_hourly rows (seconds=3600,
source='avangrid-csv') idempotently. Upsert on (ts, seconds). Hourly aggregation for analysis sums the
intervals inside each hour; never double count when both 15-min and 60-min rows cover the same hour
(prefer the finer interval).

## Importers

Registry API: every importer module defines NAME, LABEL, detect(data: bytes) -> float (0..1 confidence,
from content not filename) and parse(data: bytes) -> ImportResult. ImportResult is either
{"kind": "usage", "rows": [(ts, seconds, kwh, temp_f, received)], "source": NAME} or
{"kind": "bill", "bill": normalized_bill}. import_data picks the highest confidence > 0.5, else raises
BillingError listing what was recognised (and, for an unknown CSV, the header so the UI can offer a
mapping).

- greenbutton_xml: ESPI/Atom XML ("http://naesb.org/espi" namespace). IntervalBlock/IntervalReading with
  timePeriod start (unix) and duration (seconds), value; ReadingType gives uom (72 = Wh) and
  powerOfTenMultiplier; kWh = value * 10^multiplier / 1000 for Wh. Flow direction 19 = received
  (net metering) goes to `received`. Handle multiple IntervalBlocks and UsagePoints.
- csv_generic, header profiles (case-insensitive, trimmed headers):
  - avangrid: Name,Address,Account Number,Service,Type,Date,Start Time,End Time,Net,Units,Costs,Weather,
    Delivered,Received (Start Time is ISO with offset; Net kWh; Weather = °F). (CMP, NYSEG, RG&E, UI…)
  - smart_meter_texas: ESIID,USAGE_DATE,REVISION_DATE,USAGE_START_TIME,USAGE_END_TIME,USAGE_KWH,
    ESTIMATED_ACTUAL,CONSUMPTION_SURPLUSGENERATION (15-min, local Central time; "Surplus Generation" rows
    go to received). Mark in code and notes: built from the published format, not yet verified on a real
    export.
  - greenbutton_csv: TYPE,DATE,START TIME,END TIME,USAGE,UNITS,COST,NOTES style (PG&E-like; dates local,
    use SENSELESS_TZ). Also unverified on a real export: say so.
  - saved mappings: the user maps an unknown CSV once: {name, match_headers: [...], columns: {start,
    end?, date?, time?, kwh, temp_f?}, time_format?, timezone?, units: "kWh"|"Wh"}. Stored in settings.db
    (csv_profiles table); a CSV whose header matches a saved profile imports with it.
  Never store identifying columns (names, addresses, account/ESIID/meter ids): only the mapped values.
- bill_cmp: the current CMP parser (PDF via pypdf + text). detect = "%PDF-" and the CMP text markers.

Endpoints:
- PUT /api/billing/import — unchanged contract (raw body, X-Senseless-Upload, same-origin, 20 MiB); result
  adds `importer` (NAME) and `label`.
- POST /api/billing/import/preview — raw body; returns {importer, label, confidence, kind, sample (first 5
  normalized rows, no identifiers), header (for unknown CSVs, with any identifying-looking columns
  redacted: name/address/account/esiid/meter/service)} without saving.
- GET/POST/DELETE /api/billing/csv-profiles — saved mappings.

## Tariff packs and engine

A pack: {"id": "us-me-cmp", "utility": "Central Maine Power", "region": "US-ME", "currency": "USD",
"timezone": "America/New_York", "source": "...", "plans": [plan...], "supply": [supply...],
"taxes": [tax...]}.

A plan (delivery) or supply option is a list of charge components plus metadata
{id, label, kind: "delivery"|"supply"|"bundled", effective, expires?, source, notes?, components: [...]}:
- {"type": "fixed", "per": "month"|"day", "amount": 27.37, "includes_kwh": 50}
- {"type": "energy", "rate": 0.11959}                                  # flat $/kWh
- {"type": "tiered", "tiers": [{"up_to_kwh": 500, "rate": 0.1}, {"rate": 0.14}]}   # per bill
- {"type": "tou", "periods": [{"name": "on-peak", "rate": 0.428836, "weekdays": [0,1,2,3,4],
   "hours": [17, 21], "months": [1..12], "holidays": false}], "default_rate": 0.06147}
- {"type": "credit", "min_kwh": 1000, "max_kwh": null, "amount": -100}  # Texas usage credits
- {"type": "minimum", "amount": 10}                                      # minimum bill for this plan
- {"type": "rider", "rate": 0.003}                                       # extra $/kWh line
Taxes (pack level, applied to the bill): {"rate": 0.055, "exempt_kwh": 750, "applies_to":
["energy","tiered","tou","rider"], "fixed_taxed": false, "label": "Maine sales tax"} — CMP: per-kWh
charges of the kWh above the exemption are taxed, the fixed charge isn't (verified on 36 real bills).
A bill = chosen delivery plan + chosen supply (or one bundled plan, e.g. a Texas REP plan that includes
TDU charges) + taxes. The engine estimates for {kwh, hourly profile optional} and returns line items,
total, effective and marginal rates (marginal = difference of two estimates, as now).

us-me-cmp.json carries today's CMP Rate A, A-TOU, Electric Technology (with expires 2027-01-31 and its
source), the 2026 Standard Offer, and Maine sales tax. Settings migrate: plan "cmp-a" ->
{"pack": "us-me-cmp", "plan": "rate-a", "supply": "standard-offer-2026"}; the custom plan becomes a
user tariff (same schema) stored in settings.db; users can also POST a whole pack JSON (validated).

Endpoints: GET /api/billing/tariffs (packs: id, utility, region, plans/supply summaries), GET
/api/billing/tariffs/{pack} (full), POST /api/billing/tariffs (add/replace a user pack, validated; built-in
ids are read-only), DELETE /api/billing/tariffs/{pack} (user packs only). Settings gain pack/plan/supply.
Plan comparison (insights plans_12mo and cycle compare) compares the plans of the selected pack (all
delivery plans x the chosen supply), not a hard-coded list.

## Bills (normalized)
{start, end, kwh, amount, bill_date?, lines: [{label, kind: fixed|delivery|supply|energy|tax|credit|other,
amount, kwh?, rate?, start?, end?}], source: importer NAME}. Keep the existing columns working (amount,
kwh, start, end); store lines as JSON. A bill parser may provide check(bill) -> {model_total, diff} (CMP
keeps its own-rates check). Generic check: recompute with the selected pack when the bill period is
within the selected plan's effective dates.

## Must hold
- All existing tests pass (adapt imports/paths), plus new ones: each importer with a synthetic sample
  (XML with multiplier and received flow; SMT 15-min incl. surplus rows; Green Button CSV; a saved
  mapping), migration from meter_hourly and from old plan settings, pack validation (reject unknown
  component types, bad hours, negative rates where not credits), engine math for tiers, TOU, credits,
  minimum, taxes with exemption, Texas-style bundled plan, and CMP: reproduce the two real-bill
  regression cases in tests/test_billing.py ($157.13 at 550 kWh, $362.71 at 1,350 kWh) through the pack.
- PII: no identifying columns stored or returned (tests with fake names/ESIIDs assert absence).
- API_INDEX lists every route; notes/senseless-api.md documents them; notes/energy-providers-spec.md
  status updated; a short CONTRIBUTING-style section in notes/ on adding a tariff pack and an importer.
