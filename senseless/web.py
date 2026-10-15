"""HTTP server: health, Prometheus metrics, JSON API, and a minimal status page."""

import os
import sqlite3
import time
from pathlib import Path

from aiohttp import web

from .state import LiveState

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


def make_app(st: LiveState, db_path: Path) -> web.Application:
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
        since = time.time() - _minutes(req) * 60
        return web.json_response(_query(db_path, "SELECT ts, start_frame, frame, channel, w FROM deltas WHERE ts > ? ORDER BY start_frame", (since,)))

    async def readings(req):
        since = time.time() - _minutes(req, 10, 6 * 60) * 60
        return web.json_response(_query(db_path, "SELECT * FROM readings WHERE ts > ? ORDER BY frame", (since,)))

    async def index(_):
        return web.Response(text=STATUS_PAGE, content_type="text/html")

    app = web.Application()
    app.add_routes([
        web.get("/", index), web.get("/healthz", healthz), web.get("/metrics", metrics),
        web.get("/api/status", status), web.get("/api/deltas", deltas), web.get("/api/readings", readings),
    ])
    return app


async def serve(st: LiveState, db_path: Path) -> web.AppRunner:
    runner = web.AppRunner(make_app(st, db_path), access_log=None)
    await runner.setup()
    port = int(os.environ.get("SENSELESS_HTTP_PORT", "8780"))
    await web.TCPSite(runner, os.environ.get("SENSELESS_HTTP_HOST", "0.0.0.0"), port).start()
    return runner
