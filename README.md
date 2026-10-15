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

- `senseless collect`: long-running collector, WebSocket → SQLite + daily raw `jsonl.gz` archive, auto-reconnect
- `senseless deltas`: show recent step events
- `tools/sanitize.py`: strip account IDs from captures so you can share them

Next: pairing on/off steps into events, clustering them into loads, and a labeling loop.
See [notes/open-questions.md](notes/open-questions.md).

## Quick start

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

Tables: `readings` (one row per realtime update), `deltas` (step events), `device_power` (Sense's own
per-device attribution, written on change), `events` (everything else: timeline, device states, …).

## Contributing captures

Got a Sense monitor? Labeled captures are the most useful thing you can contribute: run something you know
at a known time and note it. Always run `tools/sanitize.py` first and read the output before sharing.

## License

MIT
