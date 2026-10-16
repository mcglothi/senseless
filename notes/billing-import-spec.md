# Billing import: CMP bills + hourly meter data (spec)

Status: CMP import implemented; provider backend supersedes the storage/pricing layout for 0.10.0
(2026-10-16). This document records the original CMP contract. See
[energy-providers-spec.md](energy-providers-spec.md) for the current contract and
[energy-providers.md](energy-providers.md) for migration/compatibility details. `meter_hourly` is migrated
to `usage_interval`; pricing comes from the CMP JSON pack. Upload response kinds stay compatible.

## Why
A real CMP account gives (a) bill PDFs with every line item and (b) a "Download My Data" CSV of hourly
meter kWh with outdoor temperature. With them Senseless can check its bill model against every bill,
compare rate plans over a full year (a 7-day on-peak sample was badly misleading: it said time-of-use
saves $25/month, a year of meter data says it costs $150/yr more), project the cycle seasonally, and show
usage vs temperature. Sense's daily kWh matched the meter within 0.1% over 650 days, so the meter data is
also a Sense accuracy check.

## Privacy (hard rules; public repo)
Both sources carry the customer's name, service address, account number, service point id, meter number
and invoice number. NEVER store, log, return or put these in tests/fixtures. Keep only timestamps, kWh,
temperature, dates, rates and dollar amounts. Tests use synthetic data with fake names ("Pat Example",
"1 Test Rd") so a regression that stores them would be caught by a test that asserts they're absent.

## Import endpoint
PUT /api/billing/import — raw body (CSV or PDF), header X-Senseless-Upload: 1, same-origin rule and size
limit (20 MiB) like the floor image upload. Detect by content (b"%PDF-" magic vs CSV header), not by name.
Response: {kind: "meter-csv"|"bill-pdf", added, updated, from, to} (or for a bill: the stored bill).
Add a pypdf dependency (pure Python) to pyproject for PDF text extraction.

### Meter CSV (Avangrid Energy Manager "Download My Data", 60-minute)
Header: Name,Address,Account Number,Service,Type,Date,Start Time,End Time,Net,Units,Costs,Weather,Delivered,Received
Example row (synthetic): Pat Example,"1 Test Rd, Town, ME 04000",1234567890,987654321,electric,2025-01-01,
2025-01-01 00:00:00-05:00,2025-01-01 00:59:59-05:00,1.597,kWh,,31,1.597,0
Store per hour: start (unix seconds, from the ISO time with its offset), kwh (Net), temp_f (Weather, may be
empty), delivered, received (for future solar). Table meter_hourly(ts INTEGER PRIMARY KEY, kwh REAL NOT
NULL, temp_f REAL, delivered REAL, received REAL) in settings.db. Upsert by ts (re-importing overlapping
files is normal). Reject rows with Units != kWh.

### Bill PDF (CMP)
Text (pypdf extract_text) contains, among PII to ignore:
- "Bill Date 10/01/2026"
- Delivery segments, one or two (two when a rate changed mid-cycle; fixed charge and 50 kWh allowance are
  prorated by days):
    Delivery Charges: Residential ( 08/29/2026 - 09/29/2026 )
    Delivery Service: 1,350 KWH
    Up to 50 KWH @$27.37 +$27.37
    1,300 KWH @$0.119590 +$155.46
  or split:
    Delivery Charges: Residential ( 06/27/2026 - 06/30/2026 )
    Delivery Service:
    Up to 6 KWH +$3.66
    205 KWH +$27.98
    Delivery Charges: Residential ( 07/01/2026 - 07/29/2026 )
    Delivery Service:
    Up to 44 KWH +$24.05
    1,493 KWH +$178.55
- "Maine Sales Tax +$3.95" then "Total Current Delivery Charges $186.78"
- Meter details row: "<meter#> 09/29/2026 41,391 08/28/2026 40,041 32 1,350" = read date, reading, prior
  read date, prior reading, days, total kWh  (use this for the bill's period and kWh)
- Supply: "Energy Charge 1,350 KWH @$0.127210 +$171.73", "Maine Sales Tax +$4.20",
  "Total New Supplier Charges $175.93", "Supplier: Standard Offer"
Store in the existing bills table (add columns: bill_date, delivery_total, supply_total, delivery_tax,
supply_tax, fixed_total, supply_rate, segments_json, source TEXT default 'manual'); amount = delivery_total +
supply_total (current charges; NOT "amount due", which can include balances). start = prior read date,
end = read date. Dedupe on (start, end). Write the text parser as a pure function parse_cmp_bill_text(text)
so tests feed synthetic text; only a thin wrapper touches pypdf.

## Analysis: GET /api/billing/insights
{ meter: {hours, from, to} | null,
  sense_vs_meter: {days, meter_kwh, sense_kwh, ratio, months: [{month, meter_kwh, sense_kwh}]} | null
     (Sense daily kWh from the History object, month scale, days both have; skip today),
  plans_12mo: {from, to, months: [{month, kwh, on_peak_share, totals: {plan_id: $}}], totals: {plan_id: $}} | null
     (last 12 complete calendar months of meter data, current tariffs from billing.PLANS + settings,
      on-peak = weekday 17-21 local; needs >= 300 days of data in the window),
  weather: {bins: [{from_f, to_f, days, kwh_per_day}]} | null  (10°F bins of daily mean temp),
  bills: [{start, end, kwh, amount, effective_cents_per_kwh, check: {model_total, diff}}]
     (model_total recomputes the bill from ITS OWN rates/segments with the tax rule in billing.py:
      5.5% x (kWh above 750, prorated across segments by kWh) x per-kWh delivery+supply; diff should be ~0)
}

## Cycle changes (billing.Billing.cycle)
- When plans_12mo is available, compare uses it (report basis: "12 months of meter data") instead of the
  7-day on-peak share; keep the 7-day share as a fallback (basis: "last 7 days").
- Seasonal projection: if the meter has the same calendar window last year, scale the recent daily rate
  for the remaining days by (last year's remaining-days avg / last year's same recent-7-days avg), clamped
  0.6-1.6. Report projection_basis.
- Calibration (calibrate=True) only uses bills whose period is on the current tariff (fixed and delivery
  rate equal to the current plan's), since older bills had other rates.

## API index, docs, tests
Add routes to API_INDEX (a test enforces it), notes/senseless-api.md section, tests in tests/test_billing_import.py
(synthetic CSV incl. DST change and overlap re-import, synthetic bill texts for single and split segments,
PII-absence assertions, insights math, cycle basis switch, upload guards). Falsify the PII test by
temporarily storing the name and seeing it fail.
