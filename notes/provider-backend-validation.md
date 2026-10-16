# Provider backend validation — 2026-10-16

Worktree: `senseless-codex-import`, branch `codex/billing-import`. Changes are uncommitted.
No `web/` files were edited by this backend task. Concurrent UI edits in that directory were preserved.

## Result and decisions

- Implemented the specified `senseless/energy/` layout: interval store; in-repo importer registry;
  Green Button XML; Avangrid, Smart Meter Texas and Green Button CSV profiles; persisted mappings;
  CMP PDF/text bill plugin; validated data-only packs and generic tariff engine.
- `billing.Billing` retains settings/cycle/bills/insights and delegates storage, parsing and pricing.
  Legacy response fields and upload kinds remain. Canonical settings/comparison IDs follow the new
  spec; legacy `cmp-*` IDs remain accepted. Imports add `importer`/`label`; estimates add `lines`.
- Idempotent migrations preserve old meter rows and bill columns, normalize legacy CMP lines/source,
  migrate settings, and persist custom rates as a component-schema user tariff. The legacy meter
  table is retained for rollback/reference and no longer used for analysis.
- Overlap analysis chooses the finest observation for each covered segment; coarse data fills gaps.
  Partial hours cannot count as complete days. Coarse energy is assumed uniform inside its interval.
- User pack selection drives comparisons, including bundled plans and TOU supply. The engine handles
  tiers, fixed monthly/daily charges, TOU, credits, minimums, riders and exemption-aware taxes. Bill,
  cycle and month analysis use actual calendar days for daily fixed charges.
- CMP own-rates checks remain independent of current settings. Generic checks respect effective dates;
  generic calibration requires a matching captured component/supply/tax snapshot. CMP calibration keeps
  its existing fixed/delivery-rate checks.
- No identifying source columns, raw upload bytes or extracted bill text are persisted. Unknown header
  labels are conservatively redacted along with identifying-looking labels. DST-ambiguous local times
  are rejected rather than guessed; explicit offsets can disambiguate them.

## Validation

- `UV_CACHE_DIR=/tmp/senseless-uv-cache uv run pytest -q --tb=short`: **414 passed; 12 failed solely
  because socket binds are sandbox-denied (`PermissionError`, errno 1)**. See exact names below.
- Follow-up `uv run pytest -q` with exactly those 12 tests deselected: **414 passed, 12 deselected**.
- New route tests invoke the real aiohttp router/handlers without sockets, covering previews, storage
  absence, mapping and pack CRUD, validation, legacy aliases, upload caps and same-origin guards.
- CMP JSON pack regressions are exact to the cent: **$157.13 at 550 kWh**, **$362.71 at 1,350 kWh**.
- Fake-only importer tests cover XML multipliers/received flow/multiple UsagePoints, SMT 15-minute
  surplus/midnight/revisions, Green Button CSV Wh conversion, saved mappings, overlap/DST and migration.
- Privacy assertion falsification succeeded: deliberately inserting the fake name `Pat Example` into a
  temporary database caused the absence assertion to fail. The temporary database was deleted.
- `git diff --check -- senseless tests notes`: passed.
- AIKB `run_v2_trial.sh codex` from the AIKB root: passed. No deployment or commit performed.

## Socket tests blocked by the sandbox

- `tests/test_api.py::test_stream_delivers_snapshot_then_events`
- `tests/test_api.py::test_shutdown_is_prompt_with_an_open_stream`
- `tests/test_api.py::test_series_and_deltas_accept_windows`
- `tests/test_api.py::test_circuits_api`
- `tests/test_billing.py::test_billing_api`
- `tests/test_billing.py::test_today_cost_uses_the_tariff`
- `tests/test_detect.py::test_loads_api_name_and_ignore`
- `tests/test_floorplans.py::test_http_api`
- `tests/test_floorplans.py::test_http_write_security_and_upload_limits`
- `tests/test_history.py::test_history_endpoint`
- `tests/test_hunt.py::test_hunt_api_long_poll_and_label_to_room`
- `tests/test_hunt.py::test_stream_snapshot_carries_hunt`

## Unverified or excluded

- Smart Meter Texas and PG&E-style Green Button CSV remain **unverified against real exports**,
  explicitly marked in code/docs. Only synthetic data was used. No live household import/migration
  or utility network access was attempted.
- The CMP Electric Technology source retains its existing “unverified against tariff sheet” note and
  2027-01-31 expiry. Published prices were preserved from the contract, not refreshed from the web.
- No jurisdiction-specific holiday calendar is inferred. The engine honors explicit profile holiday
  flags; CMP retains its prior approximation. Multiple TOU calendars require hourly data rather than
  one on-peak scalar. Expired selections are exposed, not automatically replaced.
- Browser/UI integration and UI tests are excluded as requested; Claude owns `web/`. Socket-bound
  integration tests must be rerun outside this sandbox. Backend handler tests passed here.

## Files changed by this backend task

- [senseless/billing.py](../senseless/billing.py)
- [senseless/web.py](../senseless/web.py)
- [senseless/energy/__init__.py](../senseless/energy/__init__.py)
- [senseless/energy/usage.py](../senseless/energy/usage.py)
- [senseless/energy/tariff.py](../senseless/energy/tariff.py)
- [senseless/energy/packs/us-me-cmp.json](../senseless/energy/packs/us-me-cmp.json)
- [senseless/energy/importers/__init__.py](../senseless/energy/importers/__init__.py)
- [senseless/energy/importers/greenbutton_xml.py](../senseless/energy/importers/greenbutton_xml.py)
- [senseless/energy/importers/csv_generic.py](../senseless/energy/importers/csv_generic.py)
- [senseless/energy/importers/bill_cmp.py](../senseless/energy/importers/bill_cmp.py)
- [tests/test_billing.py](../tests/test_billing.py)
- [tests/test_billing_import.py](../tests/test_billing_import.py)
- [tests/test_energy_api.py](../tests/test_energy_api.py)
- [tests/test_energy_importers.py](../tests/test_energy_importers.py)
- [tests/test_energy_tariff.py](../tests/test_energy_tariff.py)
- [notes/README.md](../notes/README.md)
- [notes/billing-import-spec.md](../notes/billing-import-spec.md)
- [notes/energy-providers-spec.md](../notes/energy-providers-spec.md)
- [notes/energy-providers.md](../notes/energy-providers.md)
- [notes/findings.md](../notes/findings.md)
- [notes/open-questions.md](../notes/open-questions.md)
- [notes/senseless-api.md](../notes/senseless-api.md)
- [notes/provider-backend-validation.md](../notes/provider-backend-validation.md)
