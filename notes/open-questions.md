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
- [x] Reject hours-long stale-on pairings when sustained leg power disproves the run or its overlap with a newer cycle (2026-10-16, findings.md); ambiguous pairs remain possible.
- [x] Reject extreme closed-duration outliers using a well-sampled unnamed pattern's own median and upper tail (2026-10-16, findings.md); sparse/broad histories remain ambiguous.
- [x] Size surge starts by covered pre/settled leg power; cluster and pair by running size, retaining run/pattern `inrush_w` (2026-10-16, findings.md).
- [x] Bounded compound offs: when no single run matches, try up to 12 recent open runs on the same legs; expose `closed_by="shared-off"` (2026-10-16, findings.md).
- [x] Recover the pool-pump replay: baseline-relative evidence rejects the stale 13:05 single, and 20% surge tolerance closes the 901 W pump singly at 14:28:10 (2026-10-16, round 3 in findings.md).
- [x] Preserve named/ignored pattern identities when historical runs resize from surge to settled watts; retain run associations before assigning resized runs (2026-10-16, review follow-up in findings.md).
- [x] Bound small-load slack, check summed overlap, and account for independently confirmed background offs in the sustained floor (2026-10-16, synthetic regressions and replay).
- [x] Treat both-leg switching as overlapping either conductor during startup sizing (2026-10-16, synthetic regressions and replay).
- [ ] Recover the probable well pump without trusting its contaminated settled window: the broader overlap guard sees an L1 −124.9 W event and conservatively leaves it unresolved (2026-10-16, review follow-up in findings.md).
- [ ] Validate against more labeled data: salt's settled window lacks coverage, and small shared closures remain questionable during program chatter despite the ten-minute start-gap limit; see round 3 in findings.md.
- [ ] Multi-step offs: some loads (a 240 V dryer here) switch on in one step and off in several partial steps;
      those runs stay "unresolved" (counted, no duration/energy). Needs subset-sum style closing.
- [ ] Same device split across two patterns when its step size varies (e.g. 47 W and 56 W, both ~13 s on L1).
      Merge by name, or cluster on duration too.
- [x] Group runs into patterns by legs, stage count and size (12%); patterns + names persist in settings.db.
- [x] Labeling loop: Loads inbox with name ideas; named patterns tag future steps.
- [x] Backend program cycles and persistent naming: replay groups the 2026-10-16 washer into one L1 envelope; fragment flags and API available (see cycles.md).
- [x] Preserve saved cycle assignments during growth; exclude reliable clean gap bridges and unrelated overlap from fragment suppression; isolate expired cycle evidence from run APIs/statistics (2026-10-16, findings.md).
- [ ] Show cycle patterns/events in the UI and hide `part_of_cycles` fragments in the Loads inbox.
- [ ] Validate cycle envelopes on more washes and actual dishwasher captures; distinguish overlapping programs and refine physical start/end estimates.
- [ ] Decompose "Always On" and "Other" by leg.
- [ ] Ground truth from other sources: Kasa/Shelly smart plugs, Emporia Vue circuit CTs (ESPHome-flashable).

## Independence from the cloud

The monitor samples at ~MHz rates and streams only processed results to Sense's cloud. Everything above
still depends on Sense's servers. Long-term questions:

- [x] What does the monitor say on the LAN? (2026-10-16, one home, Wi-Fi monitor, passive capture on the router.)
      Nothing listens: top-1000 TCP ports filtered, top-30 UDP no response. Outbound it holds **one long-lived
      TLS connection to AWS us-east-1 on :443**, ~2.8 KB/s each way (Sense's spec sheet says ~300 MB/day, which
      matches). It also polls a Philips Hue bridge over plain HTTP (`GET /api/<key>/lights`, ~every 5 s) for
      Sense's Hue integration. No mDNS/SSDP seen in 90 s. Capture point needs no extra hardware: any router you
      can run `tcpdump` on sees all of it.
- [ ] Can its cloud connection be intercepted (TLS details, pinning, client certificates)? Next: capture a
      fresh handshake (SNI, cert chain, client-cert request) after a monitor reconnect.
- [ ] What does it do with no internet? (Buffer and replay? Any local fallback?) Needs a deliberate outage test.
- [ ] Hardware: SoC, debug headers (UART/JTAG), firmware extraction. Sense's spec sheet says a **1 GHz ARM
      processor** (an application processor, so almost certainly embedded Linux). Forum posts say early monitors
      used a TI ADC/DSP and newer ones NXP parts. No FCC filing to mine: Sense Labs (grantee 2AFZS) has no
      indexed device filings, so the monitor likely uses a pre-certified Wi-Fi module (the label's
      "Contains FCC ID" line would name it). **2017 teardown** (Tisham Dhar, whatnicklife.blogspot.com/2017/12/sense-energy-monitor-teardown-sampling.html):
      TI **AM3352** (BeagleBone-class; BBB's PMIC), two 14-bit 2 MSPS TI ADCs, Altera MAX V CPLD doing the
      sampling/MAC, stacked digital + power boards, clip-together case, Molex Micro-Fit (voltage) / Nano-Fit (CT)
      inputs. If units still use a GP (non-secure) AM335x, its boot ROM can boot from SD/USB/UART per SYSBOOT
      straps, i.e. an owner could run their own Linux. Newer units may be NXP-based; check the board revision.
      Note: FCC ID 2ABAMSNSE "Sense" is Hello Inc.'s sleep tracker,
      not this monitor. ⚠️ The monitor lives in the breaker panel. De-energize and know what you're doing.
- [ ] Sense is pivoting to utility smart meters. What happens to consumer monitors if the cloud goes away?

## App

- [ ] Event timeline from deltas (not Sense's laggy timeline).
- [ ] Per-leg view, cost per load, "what just turned on?" notifications.
- [ ] Prometheus exporter / Grafana / Home Assistant outputs from the local DB.


## Energy providers

- [x] Provider backend: generic interval store/importer registry, saved CSV mappings, data-only tariff
      packs/engine, normalized CMP bills, migrations and HTTP routes (2026-10-16, synthetic tests).
- [ ] Verify Smart Meter Texas and PG&E-style Green Button CSV profiles against real exports privately;
      never commit raw household data. Both profiles are currently marked unverified.
- [ ] Supply utility-specific holiday calendars if TOU comparisons need holiday exclusions beyond
      explicit profile flags; CMP retains its prior holiday approximation.
