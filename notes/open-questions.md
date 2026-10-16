# Open questions & roadmap

Things we don't know yet, roughly in priority order. If you figure one out, move it to
[findings.md](findings.md) with a date and how you confirmed it.

## Protocol

- [x] Do 240 V loads show **simultaneous** deltas on both channels? Yes: same `start_frame`, ~equal size (2026-10-15, findings.md).
- [ ] Are `start_frame` and `frame` in a delta ever different? (Maybe for slow ramps.)
- [ ] What is `c` in `realtime_update`? What is `bridge_server` used for?
- [ ] When do `amps`, `ao_w`, `aux`, `_stats` appear? (Solar installs? Feature flags? Specific protocol version?)
- [ ] HTTP methods and request bodies for the REST endpoints (needs traffic capture; no pinning, so repack or rooted emulator).
- [ ] Does `monitors/{id}/data` or `history/usage` give finer-than-1-minute history? Can we backfill deltas?
- [ ] Rate limits / how long a feed connection stays up before Sense idles it.

## Detection (software only, on top of the feed)

- [x] Pair on/off deltas into **runs** (same legs, matching size within 15%, multi-stage ons merged): `senseless/detect.py`.
- [ ] Multi-step offs: some loads (a 240 V dryer here) switch on in one step and off in several partial steps;
      those runs stay "unresolved" (counted, no duration/energy). Needs subset-sum style closing.
- [ ] Same device split across two patterns when its step size varies (e.g. 47 W and 56 W, both ~13 s on L1).
      Merge by name, or cluster on duration too.
- [x] Group runs into patterns by legs, stage count and size (12%); patterns + names persist in settings.db.
- [x] Labeling loop: Loads inbox with name ideas; named patterns tag future steps.
- [ ] Decompose "Always On" and "Other" by leg.
- [ ] Ground truth from other sources: Kasa/Shelly smart plugs, Emporia Vue circuit CTs (ESPHome-flashable).

## Independence from the cloud

The monitor samples at ~MHz rates and streams only processed results to Sense's cloud. Everything above
still depends on Sense's servers. Long-term questions:

- [ ] What does the monitor say on the LAN? (mDNS, open ports, what it connects to.)
- [ ] Can its cloud connection be intercepted (TLS details, pinning on the device side)?
- [ ] Hardware: SoC, debug headers (UART/JTAG), firmware extraction. ⚠️ The monitor lives in the breaker
      panel. De-energize and know what you're doing.
- [ ] Sense is pivoting to utility smart meters. What happens to consumer monitors if the cloud goes away?

## App

- [ ] Event timeline from deltas (not Sense's laggy timeline).
- [ ] Per-leg view, cost per load, "what just turned on?" notifications.
- [ ] Prometheus exporter / Grafana / Home Assistant outputs from the local DB.
