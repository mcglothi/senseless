# Senseless

A local-first companion for the [Sense](https://sense.com) home energy monitor. It pulls the monitor's
realtime data into your own database, so you can do your own appliance detection and build your own views.

Sense is moving toward utility smart meters, and the consumer app hasn't changed much in years. The monitor
itself is a great sensor. Senseless is about getting more out of it, and eventually depending less on Sense's cloud.

> Not affiliated with or endorsed by Sense Labs, Inc. Uses your own account credentials against the same
> endpoints the official app uses. Use at your own risk.

## What's interesting

The realtime feed carries data the official app and most integrations ignore:

- **Per-leg power** (`channels: [L1, L2]`)
- **On-board step detection** (`deltas`): every on/off step the monitor sees, with leg, watts,
  and **AC-cycle-accurate timing** (`frame` counts 60 Hz cycles)

That's enough to fingerprint appliances yourself. A microwave, for example:

```
L2  +299 W   fan/light/turntable
L2  +1726 W  magnetron, exactly 90 cycles (1.5 s) later
L2  −1907 W  off, single step, 1770 cycles (29.5 s) after start
```

Read [`notes/`](notes/) for the full protocol write-up.

## Status

Early. Today it has:

- `senseless collect`: long-running collector, WebSocket → SQLite + daily raw `jsonl.gz` archive. Built to run
  unattended: stale-feed watchdog, re-auth on 401/403, malformed messages skipped, frame-counter reset handling,
  clean SIGTERM shutdown, raw archive retention.
- **Web UI** at `/` (React, in `web/`), installable as a phone app:
  - **Now**: live per-leg power, today's energy and cost, the last 30 minutes with every step the monitor caught,
    240 V and repeating-load spotting, always-on estimate
  - **Loads**: load patterns detected from the monitor's steps (`senseless/detect.py`), an inbox of repeating
    unnamed patterns with fingerprints and name ideas; name once and every future match carries it
  - **Timeline**: per-leg power, steps and detected runs across any window (drag to zoom, pan, inspect, name)
  - **Hunt**: Breaker Hunt for the panel: flip a breaker, see exactly what dropped on which leg, save the map
  - **History**: days, months and years from Sense's cloud history, with cost and Sense's per-device split

  Fully themeable:
  base16 themes with a built-in gallery of 570 [tinted-theming](https://github.com/tinted-theming/schemes) schemes,
  light/dark/system, accent, background, density (theming engine shared with Docket). Default theme: **Senseless**.
- HTTP API (port 8780): Prometheus `/metrics`, `/healthz`, live server-sent events `/api/stream`, JSON `/api/status`,
  `/api/today`, `/api/series?minutes=N&bucket=S`, `/api/deltas?minutes=N`, `/api/readings?minutes=N`,
  appearance `/api/themes*` and `/api/appearance`. The old plain status page is at `/status`.
- `senseless deltas`: show recent step events in the terminal
- `tools/sanitize.py`: strip account IDs from captures so you can share them
- Docker image: `ghcr.io/mcglothi/senseless`

Next: pairing on/off steps into events, clustering them into loads, and a labeling loop.
See [notes/open-questions.md](notes/open-questions.md).

## Quick start

Docker:

```bash
cp docker-compose.example.yml docker-compose.yml
printf 'SENSE_EMAIL=you@example.com\nSENSE_PASSWORD=...\n' > .env && chmod 600 .env
docker compose up -d        # then open http://localhost:8780
```

From source (the UI needs a build once: `cd web && npm ci && npm run build`):

```bash
uv sync
export SENSE_EMAIL=you@example.com SENSE_PASSWORD='...'
uv run senseless collect            # Ctrl-C to stop; data lands in ./data/
uv run senseless deltas --minutes 10
```

MFA-enabled accounts aren't supported yet. The access token is cached at `~/.config/senseless/token.json` (mode 600).

Query the DB directly:

```bash
sqlite3 data/senseless.db "select datetime(ts,'unixepoch','localtime'), channel, round(w) from deltas order by start_frame desc limit 20"
```

Settings (env): `SENSELESS_HTTP_PORT` (8780), `SENSELESS_FEED_STALE_S` (60), `SENSELESS_EXIT_AFTER_S` (900),
`SENSELESS_RAW_RETENTION_DAYS` (90), `SENSELESS_READINGS_RETENTION_DAYS` (0 = forever).

> The HTTP server has no authentication. It shows household power usage, which reveals when people are home.
> Keep it on your LAN or put it behind an authenticating reverse proxy.

Tables: `readings` (one row per realtime update), `deltas` (step events), `device_power` (Sense's own
per-device attribution, written on change), `events` (everything else: timeline, device states, …).

## Contributing captures

Got a Sense monitor? Labeled captures are the most useful thing you can contribute: run something you know
at a known time and note it. Always run `tools/sanitize.py` first and read the output before sharing.

## License

MIT
