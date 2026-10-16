"""HTTP server: health, Prometheus metrics, JSON API, and a minimal status page."""

import asyncio
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from aiohttp import web

from . import summary
from .appearance import Appearance, AppearanceError
from .billing import Billing, BillingError, MAX_IMPORT_BYTES
from .circuits import CircuitError, Circuits
from .floorplans import FloorplanError, Floorplans, IMAGE_TYPES, MAX_IMAGE_BYTES
from .history import History, HistoryError
from .hunt import Hunt, HuntError
from .state import CLOSE, LiveState

LEGS = ("L1", "L2")

# GET /api: what every endpoint does, so an agent (a voice assistant, a script) can drive the app.
API_INDEX = [
    {"method": "GET", "path": "/api/status", "does": "feed health, latest realtime update"},
    {"method": "GET", "path": "/api/stream", "does": "server-sent events: snapshot, rt (live watts + steps), hunt (session changes), timeline events"},
    {"method": "GET", "path": "/api/today", "does": "energy, cost and peak since midnight, hourly bars"},
    {"method": "GET", "path": "/api/series?minutes=", "does": "per-second leg watts for charts"},
    {"method": "GET", "path": "/api/deltas?minutes=", "does": "the monitor's on-board step detections"},
    {"method": "GET", "path": "/api/readings?minutes=", "does": "raw stored readings"},
    {"method": "GET", "path": "/api/baseline", "does": "measured always-on watts"},
    {"method": "GET", "path": "/api/loads", "does": "load patterns (size, legs, runs, energy today, name, Sense hint, part_of_cycles)"},
    {"method": "POST", "path": "/api/loads/{id}/name", "body": {"name": "str|null"}, "does": "name or unname a pattern"},
    {"method": "POST", "path": "/api/loads/{id}/ignore", "body": {"ignored": "bool"}, "does": "hide a pattern from the inbox"},
    {"method": "GET", "path": "/api/runs?minutes=", "does": "individual on/off runs for the timeline, with cycle id or null"},
    {"method": "GET", "path": "/api/cycles?minutes=", "does": "program activity cycles overlapping the window, with fingerprint, energy, pattern and name"},
    {"method": "GET", "path": "/api/cycle-patterns", "does": "cycle patterns with today and 48-hour count, kWh, last seen and median duration"},
    {"method": "POST", "path": "/api/cycle-patterns/{id}/name", "body": {"name": "str|null"}, "does": "name or unname a cycle pattern"},
    {"method": "POST", "path": "/api/cycle-patterns/{id}/ignore", "body": {"ignored": "bool"}, "does": "hide a cycle pattern from the inbox"},
    {"method": "GET", "path": "/api/history?scale=day|week|month|year&date=", "does": "Sense cloud history and per-device breakdown"},
    {"method": "GET", "path": "/api/billing", "does": "tariff settings, available rate plans (with sources), current marginal rate"},
    {"method": "POST", "path": "/api/billing", "body": {"pack": "tariff pack id", "plan": "plan id (legacy cmp-* accepted)", "supply": "supply id or null", "supply_per_kwh": "num", "billing_day": "1-31", "tax_rate": "num", "tax_exempt_kwh": "num", "calibrate": "bool"}, "does": "change tariff settings"},
    {"method": "GET", "path": "/api/billing/cycle", "does": "this billing cycle: kWh so far, projected bill with fixed/delivery/supply/tax, other plans compared"},
    {"method": "PUT", "path": "/api/billing/import", "does": "import interval usage (Green Button XML/CSV, SMT, saved mappings) or CMP bill (raw body, X-Senseless-Upload: 1, maximum 20 MiB); discard personal data"},
    {"method": "POST", "path": "/api/billing/import/preview", "does": "preview normalized usage/bill without saving; same upload guards"},
    {"method": "GET", "path": "/api/billing/csv-profiles", "does": "list saved CSV mappings"},
    {"method": "POST", "path": "/api/billing/csv-profiles", "does": "validate and save a CSV column mapping"},
    {"method": "DELETE", "path": "/api/billing/csv-profiles", "does": "delete a saved CSV mapping (?name=slug)"},
    {"method": "GET", "path": "/api/billing/tariffs", "does": "list built-in and user tariff pack summaries"},
    {"method": "GET", "path": "/api/billing/tariffs/{pack}", "does": "read a complete tariff pack"},
    {"method": "POST", "path": "/api/billing/tariffs", "does": "validate and add/replace a user tariff pack; built-in ids are read-only"},
    {"method": "DELETE", "path": "/api/billing/tariffs/{pack}", "does": "delete an unselected user tariff pack"},
    {"method": "GET", "path": "/api/billing/insights", "does": "meter coverage, Sense accuracy, 12-month plan comparison, weather bins and historical bill checks"},
    {"method": "GET", "path": "/api/billing/estimate?kwh=&on_peak_kwh=&plan=&months=", "does": "what a bill for that usage would be"},
    {"method": "GET", "path": "/api/billing/bills", "does": "bills you entered, each with the model's estimate and the difference"},
    {"method": "POST", "path": "/api/billing/bills", "body": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "kwh": "num", "amount": "num"}, "does": "enter a real bill to check the estimates"},
    {"method": "DELETE", "path": "/api/billing/bills/{id}", "does": "remove an entered bill"},
    {"method": "GET", "path": "/api/hunt[?wait=version&timeout=s]", "does": "the hunt session; with wait, long-polls until it changes. Read `prompt` aloud, offer `actions`"},
    {"method": "POST", "path": "/api/hunt/start", "body": {"mode": "breaker|device", "include_background": "bool?"}, "does": "start (or restart) a hunt; breaker: next breaker after a save. Device mode ignores small loads that cycle all day unless include_background"},
    {"method": "POST", "path": "/api/hunt/save", "body": {"breaker": "str", "name": "str?"}, "does": "breaker mode: store the measured drop for this breaker"},
    {"method": "POST", "path": "/api/hunt/label", "body": {"name": "str", "room_id": "int?"}, "does": "device mode: name the measured device's pattern; room_id also pins it on the Map"},
    {"method": "POST", "path": "/api/hunt/retry", "does": "breaker: re-baseline; device: discard this measurement and wait for a fresh on"},
    {"method": "POST", "path": "/api/hunt/stop", "does": "end the hunt"},
    {"method": "GET", "path": "/api/circuits", "does": "mapped breakers"},
    {"method": "POST", "path": "/api/circuits", "body": {"breaker": "str", "name": "str?", "l1_w": "num", "l2_w": "num"}, "does": "add a breaker by hand"},
    {"method": "DELETE", "path": "/api/circuits/{id}", "does": "remove a mapped breaker"},
    {"method": "GET", "path": "/api/property", "does": "property with buildings and nested floors, highest level first"},
    {"method": "PATCH", "path": "/api/property", "body": {"name": "str"}, "does": "rename the property"},
    {"method": "PUT", "path": "/api/property/image", "does": "upload a site image (raw body, header X-Senseless-Upload: 1)"},
    {"method": "GET", "path": "/api/property/image", "does": "the property's site image"},
    {"method": "DELETE", "path": "/api/property/image", "does": "remove the site image; returns property"},
    {"method": "POST", "path": "/api/buildings", "body": {"name": "str", "icon": "str?", "x": "0-1?", "y": "0-1?", "w": "0-1?", "h": "0-1?"}, "does": "add a building"},
    {"method": "PATCH", "path": "/api/buildings/{id}", "does": "rename / reorder / place a building"},
    {"method": "DELETE", "path": "/api/buildings/{id}", "does": "delete a building and its floors, rooms, markers and images"},
    {"method": "GET", "path": "/api/floors", "does": "floors with rooms and markers (coordinates 0-1)"},
    {"method": "POST", "path": "/api/floors", "body": {"name": "str", "building_id": "int?", "level": "-5..20?"}, "does": "add a floor"},
    {"method": "PATCH", "path": "/api/floors/{id}", "does": "rename / reorder / move a floor (building_id, level)"},
    {"method": "DELETE", "path": "/api/floors/{id}", "does": "delete a floor"},
    {"method": "PUT", "path": "/api/floors/{id}/image", "does": "upload a plan image (raw body, header X-Senseless-Upload: 1)"},
    {"method": "POST", "path": "/api/floors/{id}/rooms", "body": {"name": "str", "x": "0-1", "y": "0-1", "w": "0-1", "h": "0-1"}, "does": "add a room"},
    {"method": "PATCH", "path": "/api/rooms/{id}", "does": "rename / move a room"},
    {"method": "DELETE", "path": "/api/rooms/{id}", "does": "delete a room"},
    {"method": "POST", "path": "/api/floors/{id}/markers", "body": {"kind": "load|sense|label", "ref": "str", "icon": "str", "x": "0-1", "y": "0-1"}, "does": "place a device marker"},
    {"method": "PATCH", "path": "/api/markers/{id}", "does": "move / relabel a marker"},
    {"method": "DELETE", "path": "/api/markers/{id}", "does": "remove a marker"},
    {"method": "GET", "path": "/api/appearance", "does": "theme settings"},
    {"method": "POST", "path": "/api/appearance", "does": "change theme settings"},
    {"method": "GET", "path": "/api/themes", "does": "installed themes"},
    {"method": "GET", "path": "/api/themes/gallery", "does": "base16 themes available to install"},
    {"method": "POST", "path": "/api/themes/install", "body": {"id": "str"}, "does": "install a gallery theme"},
    {"method": "DELETE", "path": "/api/themes/{id}", "does": "remove an installed theme"},
    {"method": "GET", "path": "/api/floors/{id}/image", "does": "a floor's plan image"},
]


def _esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render_metrics(st: LiveState, db_path: Path, detector=None) -> str:
    p = st.latest
    out = []

    def m(name, help_, type_, samples):
        out.append(f"# HELP {name} {help_}")
        out.append(f"# TYPE {name} {type_}")
        for labels, v in samples:
            if v is None:
                continue
            lbl = "{" + ",".join(f'{k}="{_esc(x)}"' for k, x in labels.items()) + "}" if labels else ""
            out.append(f"{name}{lbl} {v}")

    age = st.message_age()
    m("senseless_up", "1 if the Sense feed is connected and a realtime_update arrived in the last 30s", "gauge", [({}, int(st.healthy()))])
    m("senseless_last_message_age_seconds", "Seconds since the last realtime_update", "gauge", [({}, age)])
    m("senseless_monitor_online", "Monitor online per the feed hello message", "gauge",
      [({}, None if st.monitor_online is None else int(st.monitor_online))])
    m("senseless_power_watts", "Total home power", "gauge", [({}, p.get("w"))])
    m("senseless_grid_power_watts", "Grid power (negative = exporting)", "gauge", [({}, p.get("grid_w"))])
    m("senseless_solar_power_watts", "Solar power", "gauge", [({}, p.get("solar_w"))])
    m("senseless_leg_power_watts", "Power per leg", "gauge",
      [({"leg": LEGS[i]}, w) for i, w in enumerate(p.get("channels") or [])])
    m("senseless_voltage_volts", "Voltage per leg", "gauge",
      [({"leg": LEGS[i]}, v) for i, v in enumerate(p.get("voltage") or [])])
    m("senseless_frequency_hz", "Line frequency", "gauge", [({}, p.get("hz"))])
    m("senseless_frame", "Monitor AC-cycle counter", "gauge", [({}, p.get("frame"))])
    m("senseless_device_power_watts", "Per-device power as attributed by Sense", "gauge",
      [({"device": d.get("name", "?"), "device_id": d.get("id", "?")}, d.get("w")) for d in p.get("devices") or []])
    m("senseless_deltas_total", "Step events seen", "counter",
      [({"leg": leg, "direction": d}, n) for (leg, d), n in sorted(st.deltas.items())])
    m("senseless_messages_total", "Feed messages received", "counter",
      [({"type": t}, n) for t, n in sorted(st.messages.items())])
    m("senseless_reconnects_total", "Feed reconnects", "counter", [({}, st.reconnects)])
    size = sum(f.stat().st_size for f in db_path.parent.glob(db_path.name + "*") if f.is_file())
    m("senseless_db_bytes", "SQLite database size incl. WAL", "gauge", [({}, size)])
    if detector is not None and detector.computed_at:
        loads = detector.loads(summary.day_bounds()[0])
        named = {}
        for p in loads:  # a device can span several patterns that share a name
            if p["name"]:
                n = named.setdefault(p["name"], {"running": 0, "wh": 0.0, "legs": p["legs"]})
                n["running"] |= int(bool(p["running"]))
                n["wh"] += p["wh_today"]
        m("senseless_detection_age_seconds", "Seconds since detection last recomputed", "gauge", [({}, round(time.time() - detector.computed_at, 1))])
        m("senseless_patterns", "Detected load patterns in the window", "gauge",
          [({"state": s}, sum(1 for p in loads if (p["name"] and s == "named") or (not p["name"] and p["ignored"] and s == "ignored")
                              or (not p["name"] and not p["ignored"] and s == "unnamed"))) for s in ("named", "unnamed", "ignored")])
        m("senseless_load_running", "1 while a named load is on", "gauge",
          [({"load": k, "legs": v["legs"]}, v["running"]) for k, v in sorted(named.items())])
        m("senseless_load_energy_today_wh", "Energy of a named load since local midnight", "gauge",
          [({"load": k, "legs": v["legs"]}, round(v["wh"], 1)) for k, v in sorted(named.items())])
        cycle_counts = {}
        for p in detector.cycle_loads(summary.day_bounds()[0]):
            if p["name"]:
                cycle_counts[p["name"]] = cycle_counts.get(p["name"], 0) + p["cycles_today"]
        m("senseless_cycles_today", "Named appliance cycles started since local midnight", "gauge",
          [({"name": name}, n) for name, n in sorted(cycle_counts.items())])
    return "\n".join(out) + "\n"


def _query(db_path: Path, sql: str, args=()) -> list[dict]:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in db.execute(sql, args).fetchall()]
    finally:
        db.close()


def _window(req: web.Request, default_minutes: float, cap_minutes: float) -> tuple[float, float]:
    """[start, end) in unix seconds from ?start=&end= (epoch s) or ?minutes= back from now. Capped span."""
    q = req.query
    try:
        if "start" in q or "end" in q:
            end = float(q.get("end", time.time()))
            start = float(q.get("start", end - default_minutes * 60))
        else:
            end = time.time()
            start = end - _minutes(req, default_minutes, cap_minutes) * 60
    except ValueError:
        raise web.HTTPBadRequest(text="start/end must be unix seconds")
    if end <= start:
        raise web.HTTPBadRequest(text="end must be after start")
    return max(start, end - cap_minutes * 60), end


def _minutes(req: web.Request, default: float = 10, cap: float = 24 * 60) -> float:
    try:
        return min(float(req.query.get("minutes", default)), cap)
    except ValueError:
        raise web.HTTPBadRequest(text="minutes must be a number")


STATUS_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Senseless</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#fff;--fg:#111;--mut:#666;--line:#ddd;--up:#1a7f37;--down:#c62828}
@media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--mut:#999;--line:#333;--up:#4ade80;--down:#f87171}}
body{background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif;margin:0 auto;max-width:760px;padding:16px}
h1{font-size:20px;margin:0 0 4px}.mut{color:var(--mut)}.big{font-size:32px;font-weight:600}
.row{display:flex;gap:24px;flex-wrap:wrap;margin:16px 0}table{border-collapse:collapse;width:100%}
td,th{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
.pos{color:var(--down)}.neg{color:var(--up)}
</style></head><body>
<h1>Senseless</h1><div class="mut" id="conn">connecting…</div>
<div class="row"><div><div class="mut">Total</div><div class="big" id="w">–</div></div>
<div><div class="mut">L1</div><div class="big" id="l1">–</div></div>
<div><div class="mut">L2</div><div class="big" id="l2">–</div></div>
<div><div class="mut">Voltage</div><div class="big" id="v">–</div></div></div>
<h2 style="font-size:16px">Recent steps</h2>
<table><thead><tr><th>Time</th><th>Leg</th><th>Step</th><th>Frame</th></tr></thead><tbody id="d"></tbody></table>
<p class="mut"><a href="/metrics">/metrics</a> · <a href="/api/status">/api/status</a> · <a href="/api/deltas?minutes=60">/api/deltas</a> ·
<a href="https://github.com/mcglothi/senseless">github</a></p>
<script>
const f=n=>n==null?"–":Math.round(n)+" W";
async function tick(){try{
 const s=await (await fetch("api/status")).json();
 document.getElementById("conn").textContent=(s.healthy?"● live":"○ stale")+" · last message "+(s.last_message_age_seconds??"–")+"s ago";
 document.getElementById("conn").style.color=s.healthy?"var(--up)":"var(--down)";
 const p=s.latest||{};const ch=p.channels||[];const v=p.voltage||[];
 document.getElementById("w").textContent=f(p.w);document.getElementById("l1").textContent=f(ch[0]);
 document.getElementById("l2").textContent=f(ch[1]);document.getElementById("v").textContent=v.length?v.map(x=>x.toFixed(1)).join(" / "):"–";
 const d=await (await fetch("api/deltas?minutes=60")).json();
 document.getElementById("d").innerHTML=d.slice(-25).reverse().map(r=>`<tr><td>${new Date(r.ts*1000).toLocaleTimeString()}</td><td>L${r.channel+1}</td><td class="${r.w>0?"pos":"neg"}">${r.w>0?"+":""}${r.w.toFixed(0)} W</td><td class="mut">${r.start_frame}</td></tr>`).join("");
}catch(e){document.getElementById("conn").textContent="error: "+e}}
tick();setInterval(tick,3000);
</script></body></html>"""


def _static_file(path: Path):
    async def handler(_req):
        return web.FileResponse(path, headers={"Cache-Control": "max-age=86400"})
    return handler


def _web_dir() -> Path | None:
    """The built frontend (web/dist). Docker copies it to /app/web; locally it's the repo's web/dist."""
    for d in (os.environ.get("SENSELESS_WEB_DIR"), Path(__file__).resolve().parent.parent / "web" / "dist"):
        if d and (Path(d) / "index.html").exists():
            return Path(d)
    return None


def make_app(st: LiveState, db_path: Path, appearance: Appearance | None = None, web_dir: Path | None = None,
             detector=None, circuits: Circuits | None = None, history: History | None = None,
             floorplans: Floorplans | None = None, hunt: Hunt | None = None,
             billing: Billing | None = None) -> web.Application:
    async def healthz(_):
        return web.json_response({"healthy": st.healthy(), "age": st.message_age()}, status=200 if st.healthy() else 503)

    async def metrics(_):
        return web.Response(text=render_metrics(st, db_path, detector), content_type="text/plain", charset="utf-8")

    async def status(_):
        age = st.message_age()
        return web.json_response({
            "healthy": st.healthy(), "connected": st.connected, "monitor_online": st.monitor_online,
            "last_message_age_seconds": round(age, 1) if age is not None else None,
            "uptime_seconds": round(time.time() - st.started_at), "reconnects": st.reconnects,
            "latest": st.latest,
        })

    async def deltas(req):
        start, end = _window(req, 10, 24 * 60)
        return web.json_response(_query(db_path, "SELECT ts, start_frame, frame, channel, w FROM deltas WHERE ts >= ? AND ts < ? ORDER BY start_frame",
                                        (start, end)))

    async def readings(req):
        since = time.time() - _minutes(req, 10, 6 * 60) * 60
        return web.json_response(_query(db_path, "SELECT * FROM readings WHERE ts > ? ORDER BY frame", (since,)))

    async def today(_):
        if billing is None:
            cost = (st.latest or {}).get("defaultCost")
            return web.json_response(await asyncio.to_thread(summary.today, db_path, cost))

        def run():
            # your tariff, not Sense's flat rate: energy at this cycle's marginal rate (time-of-use by hour)
            # plus today's share of the fixed charge
            r = billing.rates()
            body = summary.today(db_path, r["marginal_cents_per_kwh"])
            if body["kwh"]:
                day = datetime.fromtimestamp(body["since"], summary.local_tz()).date()
                energy, r = billing.price_today(body["kwh"], body["hourly_w"], day)
                body["cost"] = round(energy + r["fixed_per_day"] * min(1.0, (time.time() - body["since"]) / 86400), 2)
            body["cost_basis"] = r["plan_label"]
            body["fixed_per_day"] = r["fixed_per_day"]
            return body
        return web.json_response(await asyncio.to_thread(run))

    baseline_cache = {"at": 0.0, "body": None}

    async def baseline_get(_):
        # ~170k readings sorted per call: compute at most every 5 min, whoever asks
        if time.time() - baseline_cache["at"] > 300 or baseline_cache["body"] is None:
            baseline_cache["body"] = await asyncio.to_thread(summary.baseline, db_path)
            baseline_cache["at"] = time.time()
        return web.json_response(baseline_cache["body"])

    async def series(req):
        start, end = _window(req, 30, 7 * 24 * 60)
        try:
            bucket = min(max(float(req.query.get("bucket", 10)), 1), 3600)
        except ValueError:
            raise web.HTTPBadRequest(text="bucket must be a number")
        if (end - start) / bucket > 5000:  # keep responses chart-sized
            bucket = (end - start) / 5000
        return web.json_response(await asyncio.to_thread(summary.series, db_path, start, end, bucket))

    async def stream(req):
        """Server-sent events: a snapshot, then every realtime update / timeline event as it arrives."""
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                           "X-Accel-Buffering": "no"})  # nginx/NPM: don't buffer
        await resp.prepare(req)
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        st.subscribers.add(q)
        try:
            await resp.write(f"event: snapshot\ndata: {json.dumps(_snapshot())}\n\n".encode())
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    if ev is CLOSE:
                        break
                    await resp.write(f"data: {json.dumps(ev)}\n\n".encode())
                except TimeoutError:
                    await resp.write(b": keepalive\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            st.subscribers.discard(q)
        return resp

    def _snapshot():
        age = st.message_age()
        return {"healthy": st.healthy(), "connected": st.connected, "monitor_online": st.monitor_online,
                "last_message_age_seconds": round(age, 1) if age is not None else None, "latest": st.latest,
                # a reconnecting client may have missed hunt events: give it the current session
                "hunt": hunt.view() if hunt is not None else None}

    # ── appearance (same contract as Docket's) ──────────────────────────────
    def _ap():
        if appearance is None:
            raise web.HTTPNotFound(text="appearance not configured")
        return appearance

    async def _body(req):
        # Writes must be JSON from this origin: blocks cross-site "simple" POSTs (text/plain, no preflight)
        if req.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="expected application/json")
        origin = req.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != req.host:
            raise web.HTTPForbidden(text="cross-origin write refused")
        try:
            body = await req.json()
        except (ValueError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="expected a JSON body")
        if not isinstance(body, dict):  # every write takes an object; [] or null must be a 400, not a 500
            raise web.HTTPBadRequest(text="expected a JSON object")
        return body

    def _bad(e):
        return web.json_response({"error": str(e)}, status=400)

    async def themes(_):
        return web.json_response({"themes": _ap().themes()})

    async def themes_gallery(req):
        return web.json_response(await asyncio.to_thread(_ap().gallery, req.query.get("refresh") == "1"))

    async def themes_install(req):
        body = await _body(req)
        try:
            return web.json_response({"themes": await asyncio.to_thread(_ap().install, str(body.get("id", "")))})
        except AppearanceError as e:
            return _bad(e)

    async def themes_remove(req):
        origin = req.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != req.host:
            raise web.HTTPForbidden(text="cross-origin write refused")
        try:
            return web.json_response({"themes": _ap().remove(req.match_info["id"])})
        except AppearanceError as e:
            return _bad(e)

    async def appearance_get(_):
        return web.json_response(_ap().settings())

    async def appearance_post(req):
        try:
            return web.json_response(_ap().update(await _body(req)))
        except AppearanceError as e:
            return _bad(e)

    # ── loads (detected patterns) ───────────────────────────────────────────
    def _det():
        if detector is None:
            raise web.HTTPNotFound(text="detection not running")
        return detector

    def _pattern_id(req) -> int:
        try:
            return int(req.match_info["id"])
        except ValueError:
            raise web.HTTPBadRequest(text="pattern id must be an integer")

    async def loads(_):
        d = _det()
        start, _end = summary.day_bounds()
        return web.json_response({"computed_at": d.computed_at, "patterns": d.loads(start)})

    async def runs(req):
        start, end = _window(req, 180, 48 * 60)
        return web.json_response(_det().runs_between(start, end))

    async def load_name(req):
        body = await _body(req)
        name = body.get("name")
        if name is not None and not isinstance(name, str):
            return _bad("name must be a string or null")
        try:
            return web.json_response(_det().patterns.name(_pattern_id(req), name))
        except KeyError:
            raise web.HTTPNotFound(text="no such pattern")

    async def load_ignore(req):
        body = await _body(req)
        try:
            return web.json_response(_det().patterns.ignore(_pattern_id(req), bool(body.get("ignored", True))))
        except KeyError:
            raise web.HTTPNotFound(text="no such pattern")

    async def cycles(req):
        start, end = _window(req, 180, 48 * 60)
        return web.json_response(_det().cycles_between(start, end))

    async def cycle_patterns(_):
        d = _det()
        start, _end = summary.day_bounds()
        return web.json_response({"computed_at": d.computed_at, "patterns": d.cycle_loads(start)})

    async def cycle_name(req):
        body = await _body(req)
        name = body.get("name")
        if name is not None and not isinstance(name, str):
            return _bad("name must be a string or null")
        try:
            return web.json_response(_det().cycle_patterns.name(_pattern_id(req), name))
        except KeyError:
            raise web.HTTPNotFound(text="no such cycle pattern")

    async def cycle_ignore(req):
        body = await _body(req)
        ignored = body.get("ignored", True)
        if not isinstance(ignored, bool):
            return _bad("ignored must be a boolean")
        try:
            return web.json_response(_det().cycle_patterns.ignore(_pattern_id(req), ignored))
        except KeyError:
            raise web.HTTPNotFound(text="no such cycle pattern")

    # ── breaker map (Breaker Hunt) ──────────────────────────────────────────
    def _circ():
        if circuits is None:
            raise web.HTTPNotFound(text="circuits not configured")
        return circuits

    async def circuits_get(_):
        return web.json_response(_circ().all())

    async def circuits_add(req):
        try:
            return web.json_response(_circ().add(await _body(req)))
        except CircuitError as e:
            return _bad(e)

    async def circuits_remove(req):
        origin = req.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != req.host:
            raise web.HTTPForbidden(text="cross-origin write refused")
        try:
            _circ().remove(int(req.match_info["id"]))
        except ValueError:
            raise web.HTTPBadRequest(text="circuit id must be an integer")
        except KeyError:
            raise web.HTTPNotFound(text="no such circuit")
        return web.json_response({"ok": True})

    # ── floor plans ────────────────────────────────────────────────────────
    def _fp():
        if floorplans is None:
            raise web.HTTPNotFound(text="floor plans not configured")
        return floorplans

    def floorplan_handler(operation, *, write=False, delete=False, returns_result=False):
        async def handler(req):
            try:
                args = []
                if "id" in req.match_info:
                    try:
                        args.append(int(req.match_info["id"]))
                    except ValueError:
                        raise FloorplanError("id must be an integer") from None
                if write:
                    args.append(await _body(req))
                if delete:
                    origin = req.headers.get("Origin")
                    if origin and origin.split("://", 1)[-1] != req.host:
                        raise web.HTTPForbidden(text="cross-origin write refused")
                result = await asyncio.to_thread(getattr(_fp(), operation), *args)
                return web.json_response({"ok": True} if delete and not returns_result else result)
            except web.HTTPBadRequest as e:
                return _bad(e.text)
            except FloorplanError as e:
                return _bad(e)
            except KeyError:
                raise web.HTTPNotFound(text="no such building, floor, room, marker or image") from None
        return handler

    async def floor_image_put(req):
        origin = req.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != req.host:
            raise web.HTTPForbidden(text="cross-origin write refused")
        try:
            if req.headers.get("X-Senseless-Upload") != "1":
                raise FloorplanError("X-Senseless-Upload: 1 required")
            if req.content_type not in IMAGE_TYPES:
                raise FloorplanError("expected image/png, image/jpeg or image/webp")
            if req.content_length is not None and req.content_length > MAX_IMAGE_BYTES:
                raise FloorplanError("image exceeds 15 MiB")
            data = bytearray()
            async for chunk in req.content.iter_chunked(64 * 1024):
                if len(data) + len(chunk) > MAX_IMAGE_BYTES:
                    raise FloorplanError("image exceeds 15 MiB")
                data.extend(chunk)
            if "id" in req.match_info:
                try:
                    ident = int(req.match_info["id"])
                except ValueError:
                    raise FloorplanError("id must be an integer") from None
                result = await asyncio.to_thread(_fp().put_image, ident, data, req.content_type)
            else:
                result = await asyncio.to_thread(_fp().put_property_image, data, req.content_type)
            return web.json_response(result)
        except FloorplanError as e:
            return _bad(e)
        except KeyError:
            raise web.HTTPNotFound(text="no such floor") from None

    async def floor_image_get(req):
        try:
            if "id" in req.match_info:
                try:
                    ident = int(req.match_info["id"])
                except ValueError:
                    raise FloorplanError("id must be an integer") from None
                path = await asyncio.to_thread(_fp().image, ident)
            else:
                path = await asyncio.to_thread(_fp().property_image)
            return web.FileResponse(path, headers={
                "Cache-Control": "max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
            })
        except FloorplanError as e:
            return _bad(e)
        except KeyError:
            raise web.HTTPNotFound(text="no such floor image") from None

    # ── hunt (breaker / device), one shared session ─────────────────────────
    def _hunt():
        if hunt is None:
            raise web.HTTPNotFound(text="hunt not running")
        return hunt

    async def _cmd(req):
        """Hunt command body: JSON when there is one; an empty POST is fine (voice clients), but still same-origin."""
        if req.can_read_body:
            return await _body(req)
        origin = req.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != req.host:
            raise web.HTTPForbidden(text="cross-origin write refused")
        return {}

    async def hunt_get(req):
        """The session. ?wait=<version> long-polls until it changes (or ?timeout= seconds, max 55)."""
        h = _hunt()
        try:
            wait = int(req.query["wait"]) if "wait" in req.query else None
            timeout = min(55.0, max(0.0, float(req.query.get("timeout", 25))))
        except ValueError:
            return _bad("wait must be an integer version and timeout a number of seconds")
        if wait is not None and h.version <= wait:
            ev = asyncio.Event()
            h.listeners.add(ev.set)
            try:
                await asyncio.wait_for(ev.wait(), timeout)
            except TimeoutError:
                pass
            finally:
                h.listeners.discard(ev.set)
        return web.json_response(h.view())

    def hunt_cmd(fn):
        async def handler(req):
            body = await _cmd(req)
            try:
                return web.json_response(await fn(_hunt(), body))
            except (HuntError, CircuitError) as e:
                return web.json_response({"error": str(e), "hunt": _hunt().view()}, status=409 if isinstance(e, HuntError) else 400)
        return handler

    async def _start(h, b):
        return h.start(b.get("mode", "device"), include_background=bool(b.get("include_background", False)))

    async def _save(h, b):
        return {"saved": h.save(b.get("breaker"), b.get("name")), "hunt": h.view()}

    def _place(name, room_id):
        """Drop a marker for a just-labeled device into a Map room (centre, nudged off other markers)."""
        fp = _fp()
        for f in fp.all():
            room = next((r for r in f["rooms"] if r["id"] == room_id), None)
            if room is None:
                continue
            taken = sum(1 for m in f["markers"] if room["x"] <= m["x"] <= room["x"] + room["w"] and room["y"] <= m["y"] <= room["y"] + room["h"])
            dx = (taken % 3 - 1) * room["w"] * 0.22
            dy = ((taken // 3) % 3 - 1) * room["h"] * 0.22
            return fp.add_marker(f["id"], {"kind": "load", "ref": name.strip()[:80], "icon": "plug",
                                           "x": room["x"] + room["w"] / 2 + dx, "y": room["y"] + room["h"] / 2 + dy})
        raise HuntError("no such room")

    async def _label(h, b):
        room = b.get("room_id")
        if room is not None and (not isinstance(room, int) or isinstance(room, bool)):
            raise HuntError("room_id must be an integer")
        if room is not None:
            fp = _fp()
            if not any(r["id"] == room for f in await asyncio.to_thread(fp.all) for r in f["rooms"]):
                raise HuntError("no such room")
        out = h.label(b.get("name"))   # on the loop: it notifies listeners and SSE queues
        if room is not None:
            try:
                out["marker"] = await asyncio.to_thread(_place, out["pattern"]["name"], room)
            except FloorplanError as e:
                out["marker_error"] = str(e)
        out["hunt"] = h.view()
        return out

    async def _retry(h, _b):
        return h.retry()

    async def _stop(h, _b):
        return h.stop()

    async def api_index(_):
        return web.json_response({"service": "senseless", "docs": "https://github.com/mcglothi/senseless/blob/main/notes/senseless-api.md",
                                  "writes": "JSON bodies; requests from browsers must be same-origin. Errors are {\"error\": ...}.",
                                  "endpoints": API_INDEX})

    # ── billing (tariff, cycle projection, plan comparison, entered bills) ──
    def _bill():
        if billing is None:
            raise web.HTTPNotFound(text="billing not configured")
        return billing

    async def billing_get(_):
        b = _bill()
        return web.json_response({"settings": await asyncio.to_thread(b.settings), "rates": await asyncio.to_thread(b.rates),
                                  "plans": await asyncio.to_thread(b.plans), "supply": await asyncio.to_thread(b.supply_info)})

    async def billing_post(req):
        try:
            return web.json_response(await asyncio.to_thread(_bill().update, await _body(req)))
        except BillingError as e:
            return _bad(e)

    async def billing_cycle(_):
        try:
            return web.json_response(await asyncio.to_thread(_bill().cycle))
        except BillingError as e:
            return _bad(e)
        except HistoryError:
            return web.json_response({"error": "Sense history unavailable"}, status=502)

    async def billing_upload(req, preview=False):
        origin = req.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != req.host:
            raise web.HTTPForbidden(text="cross-origin write refused")
        try:
            b = _bill()
            if req.headers.get("X-Senseless-Upload") != "1":
                raise BillingError("X-Senseless-Upload: 1 required")
            if req.content_length is not None and req.content_length > MAX_IMPORT_BYTES:
                raise BillingError("upload exceeds 20 MiB")
            data = bytearray()
            async for chunk in req.content.iter_chunked(64 * 1024):
                if len(data) + len(chunk) > MAX_IMPORT_BYTES:
                    raise BillingError("upload exceeds 20 MiB")
                data.extend(chunk)
            return web.json_response(await asyncio.to_thread(b.preview_import if preview else b.import_data, bytes(data)))
        except BillingError as e:
            return _bad(e)

    async def billing_import(req):
        return await billing_upload(req)

    async def billing_preview(req):
        return await billing_upload(req, preview=True)

    def billing_write_guard(req):
        origin = req.headers.get('Origin')
        if origin and origin.split('://', 1)[-1] != req.host:
            raise web.HTTPForbidden(text='cross-origin write refused')

    async def csv_profiles_get(_):
        return web.json_response(await asyncio.to_thread(_bill().csv_profiles))

    async def csv_profiles_post(req):
        try:
            return web.json_response(await asyncio.to_thread(_bill().save_csv_profile, await _body(req)))
        except BillingError as e:
            return _bad(e)

    async def csv_profiles_delete(req):
        billing_write_guard(req)
        try:
            await asyncio.to_thread(_bill().remove_csv_profile, req.query.get('name', ''))
            return web.json_response({'ok': True})
        except KeyError:
            raise web.HTTPNotFound(text='no such CSV profile') from None

    async def tariffs_get(_):
        return web.json_response(await asyncio.to_thread(_bill().tariffs.list))

    async def tariff_get(req):
        try:
            return web.json_response(await asyncio.to_thread(_bill().tariffs.get, req.match_info['pack']))
        except BillingError:
            raise web.HTTPNotFound(text='no such tariff pack') from None

    async def tariffs_post(req):
        try:
            return web.json_response(await asyncio.to_thread(_bill().save_tariff, await _body(req)))
        except BillingError as e:
            return _bad(e)

    async def tariff_delete(req):
        billing_write_guard(req)
        try:
            await asyncio.to_thread(_bill().remove_tariff, req.match_info['pack'])
            return web.json_response({'ok': True})
        except BillingError as e:
            return _bad(e)
        except KeyError:
            raise web.HTTPNotFound(text='no such tariff pack') from None

    async def billing_insights(_):
        try:
            return web.json_response(await asyncio.to_thread(_bill().insights))
        except BillingError as e:
            return _bad(e)
        except HistoryError:
            return web.json_response({"error": "Sense history unavailable"}, status=502)

    async def billing_estimate(req):
        try:
            kwh = float(req.query["kwh"])
            on = float(req.query["on_peak_kwh"]) if "on_peak_kwh" in req.query else None
            months = float(req.query.get("months", 1))
            if not (0 <= kwh < 1e6 and 0.1 <= months <= 24) or (on is not None and not 0 <= on <= kwh):
                raise ValueError
        except (KeyError, ValueError):
            return _bad("kwh (0+), optional on_peak_kwh (0..kwh) and months (0.1-24) must be numbers")
        try:
            return web.json_response(await asyncio.to_thread(_bill().estimate, kwh, on, req.query.get("plan"), months))
        except BillingError as e:
            return _bad(e)

    async def bills_get(_):
        return web.json_response(await asyncio.to_thread(_bill().bills))

    async def bills_add(req):
        try:
            return web.json_response(await asyncio.to_thread(_bill().add_bill, await _body(req)))
        except BillingError as e:
            return _bad(e)

    async def bills_remove(req):
        origin = req.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != req.host:
            raise web.HTTPForbidden(text="cross-origin write refused")
        try:
            await asyncio.to_thread(_bill().remove_bill, int(req.match_info["id"]))
        except ValueError:
            return _bad("id must be an integer")
        except KeyError:
            raise web.HTTPNotFound(text="no such bill") from None
        return web.json_response({"ok": True})

    # ── history (Sense cloud, cached) ───────────────────────────────────────
    async def history_get(req):
        if history is None:
            raise web.HTTPNotFound(text="history not configured")
        try:
            return web.json_response(await asyncio.to_thread(history.get, req.query.get("scale", "month"), req.query.get("date", "")))
        except HistoryError as e:
            return web.json_response({"error": str(e)}, status=400 if "date" in str(e) or "scale" in str(e) or "future" in str(e) else 502)

    web_dir = web_dir or _web_dir()

    async def index(_):
        if web_dir:
            return web.FileResponse(web_dir / "index.html", headers={"Cache-Control": "no-cache"})
        return web.Response(text=STATUS_PAGE, content_type="text/html")

    async def legacy(_):
        return web.Response(text=STATUS_PAGE, content_type="text/html")

    app = web.Application()
    app.add_routes([
        web.get("/", index), web.get("/status", legacy),
        # client-side routes: reloading them must serve the app, not 404
        *[web.get(p, index) for p in ("/timeline", "/loads", "/history", "/hunt", "/map", "/map/b/{building_id}", "/map/b/{building_id}/f/{floor_id}", "/bill")],
        web.get("/healthz", healthz), web.get("/metrics", metrics),
        web.get("/api/status", status), web.get("/api/deltas", deltas), web.get("/api/readings", readings),
        web.get("/api/today", today), web.get("/api/series", series), web.get("/api/baseline", baseline_get), web.get("/api/stream", stream),
        web.get("/api/themes", themes), web.get("/api/themes/gallery", themes_gallery),
        web.post("/api/themes/install", themes_install), web.delete("/api/themes/{id}", themes_remove),
        web.get("/api/appearance", appearance_get), web.post("/api/appearance", appearance_post),
        web.get("/api/loads", loads), web.get("/api/runs", runs),
        web.post("/api/loads/{id}/name", load_name), web.post("/api/loads/{id}/ignore", load_ignore),
        web.get("/api/cycles", cycles), web.get("/api/cycle-patterns", cycle_patterns),
        web.post("/api/cycle-patterns/{id}/name", cycle_name),
        web.post("/api/cycle-patterns/{id}/ignore", cycle_ignore),
        web.get("/api/circuits", circuits_get), web.post("/api/circuits", circuits_add),
        web.delete("/api/circuits/{id}", circuits_remove),
        web.get("/api/history", history_get),
        web.get("/api", api_index),
        web.get("/api/billing", billing_get), web.post("/api/billing", billing_post),
        web.get("/api/billing/cycle", billing_cycle), web.get("/api/billing/estimate", billing_estimate),
        web.put("/api/billing/import", billing_import), web.get("/api/billing/insights", billing_insights),
        web.post('/api/billing/import/preview', billing_preview),
        web.get('/api/billing/csv-profiles', csv_profiles_get),
        web.post('/api/billing/csv-profiles', csv_profiles_post),
        web.delete('/api/billing/csv-profiles', csv_profiles_delete),
        web.get('/api/billing/tariffs', tariffs_get), web.post('/api/billing/tariffs', tariffs_post),
        web.get('/api/billing/tariffs/{pack}', tariff_get), web.delete('/api/billing/tariffs/{pack}', tariff_delete),
        web.get("/api/billing/bills", bills_get), web.post("/api/billing/bills", bills_add),
        web.delete("/api/billing/bills/{id}", bills_remove),
        web.get("/api/hunt", hunt_get),
        web.post("/api/hunt/start", hunt_cmd(_start)), web.post("/api/hunt/save", hunt_cmd(_save)),
        web.post("/api/hunt/label", hunt_cmd(_label)), web.post("/api/hunt/retry", hunt_cmd(_retry)),
        web.post("/api/hunt/stop", hunt_cmd(_stop)),
        web.get("/api/property", floorplan_handler("property")),
        web.patch("/api/property", floorplan_handler("update_property", write=True)),
        web.put("/api/property/image", floor_image_put),
        web.get("/api/property/image", floor_image_get),
        web.delete("/api/property/image", floorplan_handler("remove_property_image", delete=True, returns_result=True)),
        web.post("/api/buildings", floorplan_handler("add_building", write=True)),
        web.patch("/api/buildings/{id}", floorplan_handler("update_building", write=True)),
        web.delete("/api/buildings/{id}", floorplan_handler("remove_building", delete=True)),
        web.get("/api/floors", floorplan_handler("all")),
        web.post("/api/floors", floorplan_handler("add_floor", write=True)),
        web.patch("/api/floors/{id}", floorplan_handler("update_floor", write=True)),
        web.delete("/api/floors/{id}", floorplan_handler("remove_floor", delete=True)),
        web.put("/api/floors/{id}/image", floor_image_put),
        web.get("/api/floors/{id}/image", floor_image_get),
        web.post("/api/floors/{id}/rooms", floorplan_handler("add_room", write=True)),
        web.patch("/api/rooms/{id}", floorplan_handler("update_room", write=True)),
        web.delete("/api/rooms/{id}", floorplan_handler("remove_room", delete=True)),
        web.post("/api/floors/{id}/markers", floorplan_handler("add_marker", write=True)),
        web.patch("/api/markers/{id}", floorplan_handler("update_marker", write=True)),
        web.delete("/api/markers/{id}", floorplan_handler("remove_marker", delete=True)),
    ])
    if web_dir and (web_dir / "assets").is_dir():
        app.router.add_static("/assets", web_dir / "assets", append_version=False)
    if web_dir:
        # installable app (PWA): manifest + icons from web/public
        for name in ("manifest.webmanifest", "icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png"):
            if (web_dir / name).exists():
                app.router.add_get(f"/{name}", _static_file(web_dir / name))
    return app


async def serve(st: LiveState, db_path: Path, appearance: Appearance | None = None, detector=None,
                circuits: Circuits | None = None, history: History | None = None,
                floorplans: Floorplans | None = None, hunt: Hunt | None = None,
                billing: Billing | None = None) -> web.AppRunner:
    # shutdown_timeout: a backstop so a stuck client can never hold SIGTERM past Docker's 10 s grace
    runner = web.AppRunner(make_app(st, db_path, appearance, detector=detector, circuits=circuits, history=history, floorplans=floorplans,
                                      hunt=hunt, billing=billing),
                             access_log=None, shutdown_timeout=3)
    await runner.setup()
    port = int(os.environ.get("SENSELESS_HTTP_PORT", "8780"))
    await web.TCPSite(runner, os.environ.get("SENSELESS_HTTP_HOST", "0.0.0.0"), port).start()
    return runner
