"""HTTP server: health, Prometheus metrics, JSON API, and a minimal status page."""

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path

from aiohttp import web

from . import summary
from .appearance import Appearance, AppearanceError
from .circuits import CircuitError, Circuits
from .history import History, HistoryError
from .state import CLOSE, LiveState

LEGS = ("L1", "L2")


def _esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render_metrics(st: LiveState, db_path: Path) -> str:
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


def _web_dir() -> Path | None:
    """The built frontend (web/dist). Docker copies it to /app/web; locally it's the repo's web/dist."""
    for d in (os.environ.get("SENSELESS_WEB_DIR"), Path(__file__).resolve().parent.parent / "web" / "dist"):
        if d and (Path(d) / "index.html").exists():
            return Path(d)
    return None


def make_app(st: LiveState, db_path: Path, appearance: Appearance | None = None, web_dir: Path | None = None,
             detector=None, circuits: Circuits | None = None, history: History | None = None) -> web.Application:
    async def healthz(_):
        return web.json_response({"healthy": st.healthy(), "age": st.message_age()}, status=200 if st.healthy() else 503)

    async def metrics(_):
        return web.Response(text=render_metrics(st, db_path), content_type="text/plain", charset="utf-8")

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
        cost = (st.latest or {}).get("defaultCost")
        return web.json_response(await asyncio.to_thread(summary.today, db_path, cost))

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
                "last_message_age_seconds": round(age, 1) if age is not None else None, "latest": st.latest}

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
        *[web.get(p, index) for p in ("/timeline", "/loads", "/history", "/hunt")],
        web.get("/healthz", healthz), web.get("/metrics", metrics),
        web.get("/api/status", status), web.get("/api/deltas", deltas), web.get("/api/readings", readings),
        web.get("/api/today", today), web.get("/api/series", series), web.get("/api/stream", stream),
        web.get("/api/themes", themes), web.get("/api/themes/gallery", themes_gallery),
        web.post("/api/themes/install", themes_install), web.delete("/api/themes/{id}", themes_remove),
        web.get("/api/appearance", appearance_get), web.post("/api/appearance", appearance_post),
        web.get("/api/loads", loads), web.get("/api/runs", runs),
        web.post("/api/loads/{id}/name", load_name), web.post("/api/loads/{id}/ignore", load_ignore),
        web.get("/api/circuits", circuits_get), web.post("/api/circuits", circuits_add),
        web.delete("/api/circuits/{id}", circuits_remove),
        web.get("/api/history", history_get),
    ])
    if web_dir and (web_dir / "assets").is_dir():
        app.router.add_static("/assets", web_dir / "assets", append_version=False)
    return app


async def serve(st: LiveState, db_path: Path, appearance: Appearance | None = None, detector=None,
                circuits: Circuits | None = None, history: History | None = None) -> web.AppRunner:
    # shutdown_timeout: a backstop so a stuck client can never hold SIGTERM past Docker's 10 s grace
    runner = web.AppRunner(make_app(st, db_path, appearance, detector=detector, circuits=circuits, history=history),
                             access_log=None, shutdown_timeout=3)
    await runner.setup()
    port = int(os.environ.get("SENSELESS_HTTP_PORT", "8780"))
    await web.TCPSite(runner, os.environ.get("SENSELESS_HTTP_HOST", "0.0.0.0"), port).start()
    return runner
