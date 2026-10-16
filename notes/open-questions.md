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

- [ ] Pair on/off deltas into **events** (same channel, matching magnitude, within tolerance).
- [ ] Cluster recurring events by (channel, magnitude, multi-step shape, duration) into candidate loads.
- [ ] Labeling loop: "L2 +1726 W after +299 W, what is this?" → user names it once, it sticks.
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
