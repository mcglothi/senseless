import asyncio
from datetime import date, datetime

import pytest
from aiohttp.test_utils import TestClient, TestServer

from senseless import web
from senseless.billing import DEFAULTS, Billing, BillingError, cycle_bounds, estimate, is_on_peak
from senseless.state import LiveState
from senseless.summary import local_tz


def test_rate_a_matches_the_states_typical_bill():
    """Maine DOER's example (CMP Rate A, 550 kWh, July 2026): $157.13. Under 750 kWh: no sales tax."""
    b = estimate(dict(DEFAULTS), 550)
    assert b["total"] == pytest.approx(157.13, abs=0.01)
    assert (b["fixed"], b["tax"]) == (27.37, 0)
    assert b["delivery"] == pytest.approx(500 * 0.119590, abs=0.01) and b["supply"] == pytest.approx(550 * 0.12721, abs=0.01)


def test_tax_above_exempt_kwh_and_marginal_rate():
    s = dict(DEFAULTS)
    b = estimate(s, 1200)
    before = 27.37 + 1150 * 0.119590 + 1200 * 0.12721
    assert b["tax"] == pytest.approx(before * (450 / 1200) * 0.055, abs=0.01)
    assert b["total"] == pytest.approx(before + b["tax"], abs=0.01)
    # marginal = what the 1,201st kWh adds to this bill (its delivery+supply, taxed, plus the taxable share growing)
    total = lambda k: (27.37 + (k - 50) * 0.119590 + k * 0.12721) * (1 + max(0, k - 750) / k * 0.055)
    assert b["marginal_cents_per_kwh"] == pytest.approx((total(1201) - total(1200)) * 100, abs=0.01)
    assert estimate(s, 500)["marginal_cents_per_kwh"] == pytest.approx((0.119590 + 0.12721) * 100, abs=0.01)
    # inside the fixed charge's 50 kWh, another kWh only adds supply
    assert estimate(s, 10)["marginal_cents_per_kwh"] == pytest.approx(12.721, abs=0.01)


def test_time_of_use_and_custom_plans():
    s = dict(DEFAULTS)
    b = estimate(s, 600, on_peak_kwh=60, plan_id="cmp-a-tou")
    assert b["delivery"] == pytest.approx(60 * 0.428836 + 540 * 0.061470, abs=0.01) and b["fixed"] == 24.18
    with pytest.raises(BillingError):
        estimate(s, 600, plan_id="cmp-a-tou")
    s["custom"] = {"fixed": 10.0, "fixed_kwh": 0, "delivery": 0.1}
    assert estimate(s, 100, plan_id="custom")["total"] == pytest.approx(10 + 10 + 100 * 0.12721, abs=0.01)
    with pytest.raises(BillingError):
        estimate(s, 100, plan_id="nope")


def test_cycle_bounds_and_peak_hours():
    assert cycle_bounds(1, date(2026, 10, 16)) == (date(2026, 10, 1), date(2026, 11, 1))
    assert cycle_bounds(20, date(2026, 10, 16)) == (date(2026, 9, 20), date(2026, 10, 20))
    assert cycle_bounds(20, date(2026, 12, 25)) == (date(2026, 12, 20), date(2027, 1, 20))
    tou = {"start_hour": 17, "end_hour": 21, "weekdays_only": True}
    assert is_on_peak(datetime(2026, 10, 15, 18), tou) and not is_on_peak(datetime(2026, 10, 15, 21), tou)
    assert not is_on_peak(datetime(2026, 10, 17, 18), tou)   # Saturday


class FakeHistory:
    """40 kWh a day, 2 kWh in each on-peak hour (17-21) and the rest spread evenly."""

    def get(self, scale, d):
        day = date.fromisoformat(d)
        if scale == "month":
            n = ((day.replace(day=28) + __import__("datetime").timedelta(days=4)).replace(day=1) - day.replace(day=1)).days
            return {"kwh": [40.0] * n, "cost_cents": [474] * n}
        return {"kwh": [2.0 if 17 <= h < 21 else 32 / 20 for h in range(24)]}


def test_cycle_projection_and_compare(tmp_path):
    clock = datetime(2026, 10, 16, 12, 0, tzinfo=local_tz()).timestamp()
    b = Billing(tmp_path / "s.db", FakeHistory(), clock=lambda: clock)
    c = b.cycle()
    assert (c["start"], c["end"], c["days"]) == ("2026-10-01", "2026-11-01", 31)
    assert c["kwh_so_far"] == 16 * 40          # the 16th's partial day comes from history as-is
    assert c["sense_cost_so_far"] == 16 * 4.74
    assert c["kwh_projected"] == pytest.approx(640 + 40 * (31 - 15.5), abs=0.1)
    assert c["projected"]["total"] == pytest.approx(estimate(b.settings(), c["kwh_projected"])["total"], abs=0.01)
    # weekdays are 8/40 on-peak, weekends 0: the 7 most recent complete days include 2 weekend days
    assert c["on_peak_share"] == pytest.approx((5 * 8) / (7 * 40), abs=0.001)
    plans = {x["plan"]: x for x in c["compare"]}
    assert set(plans) == {"cmp-a", "cmp-a-tou", "cmp-et"} and plans["cmp-a"]["delta"] == 0
    b.update({"billing_day": 20})
    assert b.cycle()["start"] == "2026-09-20"


def test_settings_validation(tmp_path):
    b = Billing(tmp_path / "s.db")
    assert b.settings()["plan"] == "cmp-a"
    for bad in ({"plan": "x"}, {"billing_day": 29}, {"billing_day": 1.5}, {"tax_rate": -1}, {"supply_per_kwh": "1"},
                {"calibrate": 1}, {"nope": 1}, {"custom": {"fixed": -1}}, {"custom": {"other": 1}}, {"supplier": ""}):
        with pytest.raises(BillingError):
            b.update(bad)
    s = b.update({"plan": "custom", "custom": {"delivery": 0.2}, "supply_per_kwh": 0.15})
    assert s["custom"] == {"fixed": 0.0, "fixed_kwh": 0, "delivery": 0.2} and Billing(tmp_path / "s.db").settings()["supply_per_kwh"] == 0.15


def test_bills_check_and_calibration(tmp_path):
    clock = datetime(2026, 10, 16, 12, 0, tzinfo=local_tz()).timestamp()
    b = Billing(tmp_path / "s.db", FakeHistory(), clock=lambda: clock)
    est = estimate(b.settings(), 1200)["total"]   # a 30-day bill is one month of fixed charge and allowances
    row = b.add_bill({"start": "2026-09-01", "end": "2026-10-01", "kwh": 1200, "amount": round(est * 1.1, 2)})
    assert row["ratio"] == pytest.approx(1.1, abs=0.002) and row["diff"] == pytest.approx(est * 0.1, abs=0.02)
    assert b.calibration() == pytest.approx(1.1, abs=0.002)
    b.update({"calibrate": True})
    c = b.cycle()
    assert c["projected"]["calibrated_total"] == pytest.approx(c["projected"]["total"] * c["calibration"], abs=0.01)
    for bad in ({"start": "x", "end": "2026-10-01", "kwh": 1, "amount": 1}, {"start": "2026-09-01", "end": "2026-09-03", "kwh": 1, "amount": 1},
                {"start": "2026-09-01", "end": "2026-10-01", "kwh": -1, "amount": 1}):
        with pytest.raises(BillingError):
            b.add_bill(bad)
    b.remove_bill(row["id"])
    with pytest.raises(KeyError):
        b.remove_bill(row["id"])


def test_billing_api(tmp_path):
    async def go():
        clock = datetime(2026, 10, 16, 12, 0, tzinfo=local_tz()).timestamp()
        bill = Billing(tmp_path / "s.db", FakeHistory(), clock=lambda: clock)
        app = web.make_app(LiveState(), tmp_path / "t.db", billing=bill)
        async with TestClient(TestServer(app)) as c:
            body = await (await c.get("/api/billing")).json()
            assert body["settings"]["plan"] == "cmp-a" and "cmp-a-tou" in body["plans"] and body["rates"]["marginal_cents_per_kwh"] > 24
            assert (await c.post("/api/billing", json={"billing_day": 40})).status == 400
            assert (await c.post("/api/billing", json={"billing_day": 12}, headers={"Origin": "http://evil.example"})).status == 403
            assert (await (await c.post("/api/billing", json={"billing_day": 12})).json())["billing_day"] == 12
            r = await (await c.get("/api/billing/estimate?kwh=550")).json()
            assert r["total"] == pytest.approx(157.13, abs=0.01)
            assert (await c.get("/api/billing/estimate?kwh=abc")).status == 400
            assert (await c.get("/api/billing/estimate?kwh=600&plan=cmp-a-tou")).status == 400   # needs on_peak_kwh
            cyc = await (await c.get("/api/billing/cycle")).json()
            assert cyc["start"] == "2026-10-12" and cyc["projected"]["total"] > 0
            row = await (await c.post("/api/billing/bills", json={"start": "2026-09-01", "end": "2026-10-01", "kwh": 1100, "amount": 320.5})).json()
            assert len(await (await c.get("/api/billing/bills")).json()) == 1
            assert (await c.delete(f"/api/billing/bills/{row['id']}", headers={"Origin": "http://evil.example"})).status == 403
            assert (await c.delete(f"/api/billing/bills/{row['id']}")).status == 200
            assert (await c.delete(f"/api/billing/bills/{row['id']}")).status == 404
    asyncio.run(go())


def test_today_cost_uses_the_tariff(tmp_path):
    from senseless.store import Store
    async def go():
        Store(tmp_path / "t.db")
        bill = Billing(tmp_path / "s.db")
        app = web.make_app(LiveState(), tmp_path / "t.db", billing=bill)
        async with TestClient(TestServer(app)) as c:
            t = await (await c.get("/api/today")).json()
            assert t["cost_cents_per_kwh"] == bill.rates()["marginal_cents_per_kwh"] and t["cost_basis"].startswith("CMP Rate A")
    asyncio.run(go())


def test_bill_months_and_tou_bills(tmp_path):
    from senseless.billing import bill_months
    assert bill_months(date(2026, 2, 1), date(2026, 3, 1)) == 1.0     # February is still one bill
    assert bill_months(date(2026, 9, 1), date(2026, 9, 16)) == pytest.approx(15 / 30.44)
    b = Billing(tmp_path / "s.db")
    b.update({"plan": "cmp-a-tou"})
    a = b.add_bill({"start": "2026-09-01", "end": "2026-10-01", "kwh": 1000, "amount": 250})
    assert a["estimate"] is None and b.calibration() is None             # no on-peak kWh: not guessed
    c = b.add_bill({"start": "2026-08-01", "end": "2026-09-01", "kwh": 1000, "amount": 250, "on_peak_kwh": 0})
    assert c["estimate"] == estimate(b.settings(), 1000, 0, "cmp-a-tou")["total"]
    with pytest.raises(BillingError):
        b.add_bill({"start": "2026-07-01", "end": "2026-08-01", "kwh": 10, "amount": 5, "on_peak_kwh": 11})


class GappyHistory(FakeHistory):
    """Like FakeHistory but Oct 5 is missing, and asking for a future month fails like the real History."""

    def get(self, scale, d):
        day = date.fromisoformat(d)
        if day > date(2026, 10, 16):
            raise AssertionError(f"asked for the future: {d}")
        body = super().get(scale, d)
        if scale == "month" and day.month == 10:
            body["kwh"][4] = None
        return body


def test_cycle_spanning_months_and_missing_days(tmp_path):
    clock = datetime(2026, 10, 16, 12, 0, tzinfo=local_tz()).timestamp()
    b = Billing(tmp_path / "s.db", GappyHistory(), clock=lambda: clock)
    b.update({"billing_day": 10})                 # Oct 10 -> Nov 10: November hasn't started
    c = b.cycle()
    assert (c["start"], c["end"]) == ("2026-10-10", "2026-11-10")
    b.update({"billing_day": 1})
    c = b.cycle()
    assert c["missing_days"] == ["2026-10-05"]
    assert c["kwh_so_far"] == 15 * 40
    assert c["kwh_projected"] == pytest.approx(15 * 40 + 40 + 40 * (31 - 15.5), abs=0.1)   # the gap is filled, not lost


def test_tou_today_priced_by_hour(tmp_path):
    clock = datetime(2026, 10, 16, 12, 0, tzinfo=local_tz()).timestamp()
    b = Billing(tmp_path / "s.db", FakeHistory(), clock=lambda: clock)
    b.update({"plan": "cmp-a-tou"})
    b.cycle()
    r = b.rates()
    thursday = date(2026, 10, 15)
    all_off = b.price_today(10, [500 if h < 17 else 0 for h in range(24)], thursday)[0]
    all_on = b.price_today(10, [500 if 17 <= h < 21 else 0 for h in range(24)], thursday)[0]
    assert all_off == pytest.approx(10 * r["marginal_off_cents"] / 100) and all_on == pytest.approx(10 * r["marginal_on_cents"] / 100)
    assert all_on > 2.5 * all_off   # (42.88 + 12.72)¢ vs (6.15 + 12.72)¢
