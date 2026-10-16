"""Hunt sessions: find what each breaker feeds (breaker mode) or what each device draws (device mode).

One shared session for the house, driven from the server so the app and any API client (a voice
assistant walking the house with you) see and steer the same thing. The collector feeds every live
reading in; commands (start, save, label, retry, stop) come from the HTTP API. Every state change bumps
`version` and notifies listeners (SSE publish, long-poll waiters). Each view carries a short `prompt`
meant to be read aloud.

Breaker mode: settle, average a baseline, then report what dropped on which leg until saved.
Device mode: settle, then wait for an on step (the monitor's own step detections), measure it, wait for
the matching off, and repeat as many cycles as you like; label() names the detector pattern of that size
so every future run of it carries the name.
"""

import statistics
import time
from collections import deque

from .detect import to_steps

SETTLE_S = 4.0          # let the panel settle after a breaker switches back on
BASELINE_S = 3.0        # baseline / current-level averaging window
CAPTURE_S = 2.5         # steps this close together are one switch (240 V legs, multi-stage starts)
MIN_STEP_W = 30         # the detector ignores smaller steps, so patterns don't exist below it
NOISE_W = 15            # breaker mode: leg changes below this are noise
OFF_TOL = 0.25          # device mode: an off within 25% (or OFF_ABS_W) of the on closes the cycle
OFF_ABS_W = 25
SENSE_LOOK_S = 30       # Sense's own detection lags; watch its device list this long after an on
STALE_S = 30
# Background chatter: small loads that cycle all day (a 35 W relay hundreds of times) would otherwise
# be "the device" seconds into every hunt. Patterns this frequent and short are ignored by default.
CHATTER_MIN_RUNS = 40       # runs in the detector's 48 h window
CHATTER_MAX_S = 600         # median on-time
CHATTER_MAX_W = 300
CHATTER_TOL = 0.12
MODES = ("breaker", "device")
IGNORED_SENSE = {"Always On", "Other"}


class HuntError(ValueError):
    pass


def _sentence(note: str) -> str:
    return note[:1].upper() + note[1:] + "."


def _avg(xs):
    return sum(xs) / len(xs) if xs else None


def name_ideas(w: float, legs: str, multi: bool, dur_s: float | None) -> list[str]:
    """Naming ideas from size, legs, stages and on-time (same table as web/src/ui/loads.js nameIdeas)."""
    dur = dur_s or 0
    if legs == "both":
        if w > 3500:
            return ["Electric range", "EV charger", "Water heater"]
        if w > 1500:
            return ["Dryer", "Heat pump", "Water heater"]
        return ["A/C compressor", "Well pump", "Heat pump fan"]
    if w >= 1200:
        return ["Microwave", "Space heater", "Hair dryer"] if multi else ["Kettle", "Toaster", "Space heater"]
    if w >= 600:
        return ["Dishwasher heat", "Dehumidifier", "Space heater"] if dur > 600 else ["Coffee maker", "Vacuum", "Microwave (low)"]
    if w >= 250:
        return ["Refrigerator", "Dehumidifier", "Sump pump"] if dur > 120 else ["Garage door", "Sump pump", "Blender"]
    if w >= 80:
        return ["Freezer", "Furnace blower", "TV"] if dur > 600 else ["Furnace inducer", "Bathroom fan", "Lights"]
    return ["Relay / cycling device", "Aquarium heater", "Printer"] if dur < 60 else ["Lights", "Router / network", "Fan"]


def breaker_drop(baseline: dict, current: dict, deltas: list[dict]) -> dict:
    """What changed since the baseline (port of web/src/ui/hunt.js huntDrop): the monitor's own steps
    when they explain most of the measured leg change, else the smoothed change itself."""
    since = [d for d in deltas if d["t"] > baseline["t"]]
    steps = {leg: sum(d["w"] for d in since if d["leg"] == n) for leg, n in (("l1", 1), ("l2", 2))}
    change = {leg: (current[leg] - baseline[leg]) if current.get(leg) is not None and baseline.get(leg) is not None else None
              for leg in ("l1", "l2")}

    def pick(leg):
        s, c = steps[leg], change[leg]
        if c is None:
            return s
        if abs(c) < NOISE_W:
            return s if abs(s) >= NOISE_W else 0
        # steps only win when they point the same way as the measured change
        same = (s > 0) == (c > 0) and s != 0
        return s if same and abs(s) >= abs(c) * 0.6 else c

    l1, l2 = pick("l1"), pick("l2")
    both = abs(l1) >= 100 and abs(l2) >= 100 and (l1 > 0) == (l2 > 0) and \
        abs(abs(l1) - abs(l2)) / max(abs(l1), abs(l2)) <= 0.25
    return {"l1": round(l1, 1), "l2": round(l2, 1), "total": round(l1 + l2, 1), "steps": len(since), "both": both,
            "meaningful": abs(l1 + l2) >= NOISE_W}


class Hunt:
    def __init__(self, circuits=None, detector=None, healthy=None, publish=None):
        self.circuits = circuits
        self.detector = detector
        self._healthy = healthy or (lambda: True)
        self._publish = publish
        self.listeners: set = set()
        self.version = 0
        self.epoch = time.time()           # with version: orders views across server restarts
        self.t = 0.0                       # server time of the latest reading
        self.readings: deque = deque()     # (t, l1, l2), last 60 s
        self.devices: dict[str, float] = {}
        self.last = None                   # what the previous save/label stored (for the prompt)
        self._last_mode = None
        self._reset(None, "idle")

    # ── state ───────────────────────────────────────────────────────────
    def _reset(self, mode, phase):
        self.mode, self.phase = mode, phase
        self.settle_until = 0.0
        self.baseline = None
        self.deltas: list[dict] = []       # breaker mode: every step since the baseline
        self.capture = None                # device mode: steps of the switch being measured
        self.on = None                     # device mode: the current cycle's on
        self.cycles: list[dict] = []
        self.noise = 0
        self.note = None
        self._drop_key = None
        self.background: list[dict] = []    # device mode: chatter patterns being ignored
        self.ignored = 0

    def _changed(self):
        self.version += 1
        view = self.view()
        if self._publish:
            self._publish({"type": "hunt", "hunt": view})
        for fn in list(self.listeners):
            fn()

    def _level(self, start: float, end: float) -> dict:
        rs = [r for r in self.readings if start <= r[0] <= end]
        return {"l1": _avg([r[1] for r in rs]), "l2": _avg([r[2] for r in rs]), "n": len(rs)}

    def _require_live(self):
        if not self._healthy() or not self.t:
            raise HuntError("the live feed is stale; wait for it to reconnect")

    # ── input from the collector ────────────────────────────────────────
    def feed(self, t: float, channels, deltas=(), devices=()) -> None:
        if not channels or len(channels) < 2 or channels[0] is None:
            return
        self.t = t
        self.readings.append((t, float(channels[0]), float(channels[1])))
        while self.readings and self.readings[0][0] < t - 60:
            self.readings.popleft()
        steps = [{"t": t, "leg": d["channel"] + 1, "w": float(d["w"]), "frame": d.get("start_frame", d.get("frame"))}
                 for d in deltas or () if d.get("channel") in (0, 1) and isinstance(d.get("w"), (int, float))]
        if self.on is not None:
            self._watch_sense(t, devices)
        self.devices = {d.get("name"): float(d.get("w") or 0) for d in devices or () if d.get("name")}
        if self.phase == "idle":
            return
        if self.phase == "settling":
            if t >= self.settle_until:
                self._settled()
            return
        if self.mode == "breaker":
            self._breaker_feed(steps)
        else:
            self._device_feed(t, steps)

    def _settled(self):
        b = self._level(self.t - BASELINE_S, self.t)
        if b["n"] < 3:
            self.phase, self.note = "idle", "no live readings yet; try again in a moment"
            self._changed()
            return
        self.baseline = {"t": self.t, "l1": b["l1"], "l2": b["l2"]}
        self.phase = "watching" if self.mode == "breaker" else "ready"
        self._changed()

    # ── breaker mode ────────────────────────────────────────────────────
    def drop(self):
        if self.mode != "breaker" or self.phase != "watching" or not self.baseline:
            return None
        return breaker_drop(self.baseline, self._level(self.t - BASELINE_S, self.t), self.deltas)

    def _breaker_feed(self, steps):
        if self.phase != "watching":
            return
        self.deltas.extend(steps)
        d = self.drop()
        key = (d["meaningful"], d["total"] > 0, round(d["l1"] / 10), round(d["l2"] / 10), d["both"], d["steps"])
        if key != self._drop_key:
            self._drop_key = key
            self._changed()

    def save(self, breaker, name=None) -> dict:
        if self.mode != "breaker" or self.phase != "watching":
            raise HuntError("nothing to save: start a breaker hunt and switch a breaker off first")
        d = self.drop()
        if not d["meaningful"] or d["total"] > 0:
            raise HuntError("no drop measured yet: switch a breaker off first")
        if self.circuits is None:
            raise HuntError("breaker storage is not configured")
        saved = self.circuits.add({"breaker": breaker, "name": name, "l1_w": round(d["l1"]), "l2_w": round(d["l2"])})
        self._reset("breaker", "flipback")
        self.note = None
        self.last = {"breaker": saved["breaker"], "name": saved.get("name"), "l1_w": saved["l1_w"], "l2_w": saved["l2_w"]}
        self._changed()
        return saved

    # ── device mode ─────────────────────────────────────────────────────
    def _chatter(self) -> list[dict]:
        if self.detector is None or not hasattr(self.detector, "loads"):
            return []
        out = []
        for p in self.detector.loads(time.time() - 86400):
            dur = p.get("median_duration_s")
            if p["legs"] in ("L1", "L2") and p.get("runs_window", 0) >= CHATTER_MIN_RUNS and dur is not None and \
                    dur <= CHATTER_MAX_S and p["center_w"] <= CHATTER_MAX_W:
                out.append({"legs": p["legs"], "w": round(p["center_w"], 1), "runs_48h": p["runs_window"], "name": p.get("name")})
        return sorted(out, key=lambda c: -c["runs_48h"])

    def _is_chatter(self, s) -> bool:
        leg = f"L{s['leg']}"
        return any(c["legs"] == leg and abs(abs(s["w"]) - c["w"]) <= max(CHATTER_TOL * c["w"], 4) for c in self.background)

    def _device_feed(self, t, steps):
        big = [s for s in steps if abs(s["w"]) >= MIN_STEP_W]
        if self.background:
            kept = [s for s in big if not self._is_chatter(s)]
            self.ignored += len(big) - len(kept)
            big = kept
        if self.capture is None:
            for s in big:
                if self.phase in ("ready", "measured") and s["w"] > 0:
                    self.capture = {"kind": "on", "t": t, "steps": []}
                    break
                if self.phase == "on" and s["w"] < 0 and self._on_legs(s["leg"]):
                    self.capture = {"kind": "off", "t": t, "steps": []}
                    break
            else:
                if big:  # steps that can't start anything here: some other load switching
                    self.noise += len(big)
                    ups = [x for x in big if x["w"] > 0]
                    if self.phase == "on" and ups and not self.cycles:
                        self.note = (f"another {round(max(x['w'] for x in ups))} W load turned on too; "
                                     "if that one is yours, retry and switch it again")
                        self._changed()
                return
        if t - self.capture["t"] < CAPTURE_S:
            self.capture["steps"].extend(big)
            return
        cap, self.capture = self.capture, None
        if cap["kind"] == "on":
            self._took_on(cap)
        else:
            self._took_off(cap)
        # steps in the update that closed the window belong to the next switch
        if big:
            self._device_feed(t, steps)

    def _on_legs(self, leg: int) -> bool:
        legs = self.on["legs"]
        return legs == "both" or legs == f"L{leg}"

    def _took_on(self, cap):
        # classify with the detector's own grouping (240 V pairing, multi-stage starts), so the pattern
        # we name is the one future runs of this device will join
        rows = [{"start_frame": x["frame"] if x["frame"] is not None else i, "channel": x["leg"] - 1, "w": x["w"], "ts": x["t"]}
                for i, x in enumerate(cap["steps"])]
        ons = [st for st in to_steps(rows) if st.w > 0]
        if not ons:
            return
        best = max(ons, key=lambda st: st.w)
        legs, w = best.legs, best.w
        if len(ons) > 1:
            self.noise += len(ons) - 1   # other loads switching in the same moment, ignored
        ups = [x for x in cap["steps"] if x["w"] > 0]
        l1 = sum(x["w"] for x in ups if x["leg"] == 1 and legs in ("L1", "both"))
        l2 = sum(x["w"] for x in ups if x["leg"] == 2 and legs in ("L2", "both"))
        groups = max(1, len(best.stages))
        if self.phase == "measured" and (legs != self.cycles[-1]["legs"] or
                                         abs(w - self.cycles[-1]["on_w"]) > max(OFF_TOL * self.cycles[-1]["on_w"], OFF_ABS_W)):
            # another load starting while we wait for a repeat: don't fold it into this device
            self.noise += 1
            self.note = f"saw {round(w)} W turn on, which isn't this device; retry to measure a different one"
            self._changed()
            return
        before = self._level(cap["t"] - BASELINE_S, cap["t"] - 0.01)
        after = self._level(self.t - 1.5, self.t)
        level = None
        if before["n"] and after["n"]:
            level = round(sum(after[k] - before[k] for k in ("l1", "l2") if legs == "both" or k == legs.lower()), 1)
        self.on = {"t": cap["t"], "legs": legs, "w": round(w, 1), "l1": round(l1, 1), "l2": round(l2, 1),
                   "stages": min(groups, 2), "level_change": level, "sense": {}, "sense_base": dict(self.devices)}
        self.phase, self.note = "on", None
        self._changed()
        # switched off again inside the same window (a quick tap): that's this cycle's off
        downs = [x for x in cap["steps"] if x["w"] < 0 and self._on_legs(x["leg"])]
        if downs:
            self._took_off({"t": downs[0]["t"], "steps": downs})

    def _took_off(self, cap):
        downs = [s for s in cap["steps"] if s["w"] < 0 and self._on_legs(s["leg"])]
        off = -sum(s["w"] for s in downs)
        if abs(off - self.on["w"]) <= max(OFF_TOL * self.on["w"], OFF_ABS_W):
            self.cycles.append({"on_w": self.on["w"], "off_w": round(off, 1), "legs": self.on["legs"], "stages": self.on["stages"],
                                "duration_s": round(cap["t"] - self.on["t"], 1), "level_change": self.on["level_change"],
                                "sense": sorted(self.on["sense"], key=lambda n: -self.on["sense"][n])})
            self.on, self.phase, self.note = None, "measured", None
        else:
            self.noise += 1
            self.note = f"saw {round(off)} W switch off, which doesn't match {round(self.on['w'])} W; still waiting"
        self._changed()

    def _watch_sense(self, t, devices):
        """Sense devices whose estimate rose after our on step: Sense's own guess at what it is."""
        if t - self.on["t"] > SENSE_LOOK_S:
            return
        for d in devices or ():
            name, w = d.get("name"), float(d.get("w") or 0)
            if not name or name in IGNORED_SENSE:
                continue
            rise = w - self.on["sense_base"].get(name, 0.0)
            if rise >= 20 and abs(rise - self.on["w"]) <= 0.5 * max(rise, self.on["w"]):
                self.on["sense"][name] = max(self.on["sense"].get(name, 0), round(rise))

    def measurement(self) -> dict | None:
        """What's been measured so far (finished cycles, plus the on of a cycle still running)."""
        ons = [c["on_w"] for c in self.cycles] + ([self.on["w"]] if self.on else [])
        if not ons:
            return None
        src = self.cycles + ([{"legs": self.on["legs"], "stages": self.on["stages"], "sense": list(self.on["sense"])}] if self.on else [])
        legs = statistics.mode(c["legs"] for c in src)
        stages = max(c["stages"] for c in src)
        w = round(statistics.median(ons), 1)
        durs = [c["duration_s"] for c in self.cycles]
        sense = []
        for c in src:
            sense += [n for n in c["sense"] if n not in sense]
        m = {"w": w, "legs": legs, "stages": stages, "cycles": len(self.cycles), "on_sizes": ons,
             "off_sizes": [c["off_w"] for c in self.cycles], "durations_s": durs,
             "consistent": len(ons) >= 2 and (max(ons) - min(ons)) <= max(0.1 * w, 15),
             "sense_guess": sense, "ideas": name_ideas(w, legs, stages > 1, statistics.median(durs) if durs else None),
             "matches": self._matches(legs, stages, w)}
        return m

    def _matches(self, legs, stages, w) -> list[dict]:
        if self.detector is None:
            return []
        out = []
        for p in self.detector.patterns.all():
            if p["legs"] != legs or p.get("ignored"):
                continue
            err = abs(p["center_w"] - w) / max(p["center_w"], w)
            same = p["stages"] == stages and err <= 0.12
            if same or (p.get("name") and err <= 0.35):
                out.append({"pattern": p["id"], "name": p.get("name"), "center_w": round(p["center_w"], 1),
                            "seen_48h": p.get("n", 0), "same_pattern": same})
        out.sort(key=lambda m: (not m["same_pattern"], abs(m["center_w"] - w)))
        return out[:5]

    def label(self, name: str) -> dict:
        if self.mode != "device" or self.phase not in ("on", "measured"):
            raise HuntError("nothing measured yet: turn the device on first")
        if not isinstance(name, str) or not name.strip():
            raise HuntError("name must be a non-empty string")
        if self.detector is None:
            raise HuntError("detection is not running")
        m = self.measurement()
        pattern = self.detector.patterns.find_or_create(m["legs"], m["stages"], m["w"])
        named = self.detector.patterns.name(pattern["id"], name)
        bg = self.background
        self._reset("device", "ready")
        self.background = bg
        self.last = {"name": named["name"], "pattern": named["id"], "w": m["w"], "legs": m["legs"]}
        self._changed()
        return {"pattern": named, "measurement": m}

    # ── commands ────────────────────────────────────────────────────────
    def start(self, mode: str, include_background: bool = False) -> dict:
        if mode not in MODES:
            raise HuntError("mode must be breaker or device")
        self._require_live()
        self._reset(mode, "settling")
        if mode == "device" and not include_background:
            self.background = self._chatter()
        self._include_bg = include_background
        if mode != self._last_mode:
            self.last = None
        self._last_mode = mode
        self.settle_until = self.t + SETTLE_S
        self._changed()
        return self.view()

    def retry(self) -> dict:
        """Breaker: re-baseline. Device: throw away this device's measurement and wait for a fresh on."""
        if self.mode is None or self.phase == "idle":
            raise HuntError("no hunt running")
        if self.mode == "breaker":
            return self.start("breaker")
        self._require_live()
        bg = self.background
        self._reset("device", "ready")
        self.background = bg
        self._changed()
        return self.view()

    def stop(self) -> dict:
        self._reset(None, "idle")
        self.last = None
        self._changed()
        return self.view()

    # ── view ────────────────────────────────────────────────────────────
    def view(self) -> dict:
        live = bool(self._healthy())
        v = {"version": self.version, "epoch": self.epoch, "mode": self.mode, "phase": self.phase, "live": live, "note": self.note,
             "last": self.last, "baseline": self.baseline, "noise_steps": self.noise}
        if self.mode == "breaker":
            v["drop"] = self.drop()
        if self.mode == "device":
            v["on"] = {k: self.on[k] for k in ("legs", "w", "l1", "l2", "stages", "level_change")} if self.on else None
            v["measurement"] = self.measurement()
            v["capturing"] = self.capture is not None
            v["ignoring"] = self.background
            v["ignored_steps"] = self.ignored
        v["prompt"] = self._prompt(v)
        v["actions"] = self._actions()
        return v

    def _actions(self) -> list[str]:
        p, m = self.phase, self.mode
        if p == "idle":
            return ["start"]
        if m == "breaker":
            return {"settling": ["stop"], "watching": ["save", "retry", "stop"], "flipback": ["start", "stop"]}.get(p, ["stop"])
        return {"settling": ["stop"], "ready": ["retry", "stop"], "on": ["label", "retry", "stop"],
                "measured": ["label", "retry", "stop"]}.get(p, ["stop"])

    @staticmethod
    def _known(m) -> str | None:
        return next((x["name"] for x in (m or {}).get("matches", []) if x["name"] and x["same_pattern"]), None)

    def _prompt(self, v) -> str:
        p, last = self.phase, v["last"]
        stale = "" if v["live"] else "The live feed is stale, so readings may be old. "
        if p == "idle":
            return stale + (_sentence(self.note) if self.note else "No hunt running. Start a breaker hunt or a device hunt.")
        if p == "settling":
            return stale + "Measuring the baseline. Hold still for a few seconds."
        if self.mode == "breaker":
            if p == "flipback":
                return f"Saved breaker {last['breaker']}. Switch it back on, then start the next one."
            d = v["drop"]
            if not d or not d["meaningful"]:
                return stale + "Listening. Switch off one breaker."
            if d["total"] > 0:
                return stale + f"Load went up by {round(d['total'])} watts, so something turned on. Re-baseline, then flip the breaker."
            where = "on both legs, a 240 volt breaker" if d["both"] else f"on L{1 if abs(d['l1']) > abs(d['l2']) else 2}"
            return stale + f"Dropped {round(-d['total'])} watts {where}. Which breaker number was that, and what's on it?"
        done = f"Saved {last['name']}. " if last else ""
        if p == "ready":
            bg = len(self.background)
            ignoring = f", ignoring {bg} small background load{'s' if bg != 1 else ''}" if bg else ""
            return stale + done + f"Go to a device and turn it on. I'm watching for it{ignoring}."
        if p == "on":
            on = v["on"]
            legs = "on both legs, 240 volts" if on["legs"] == "both" else f"on {on['legs']}"
            n = len(self.cycles)
            lead = f"Turned on: {round(on['w'])} watts {legs}." if n == 0 else f"On again: {round(on['w'])} watts."
            named = self._known(v["measurement"])
            known = f" That looks like {named}, already named." if named else ""
            tail = f" {_sentence(self.note)}" if self.note else ""
            return stale + lead + known + " Now turn it off, or tell me what it is." + tail
        m = v["measurement"]
        guess = f" Sense thinks it might be {m['sense_guess'][0]}." if m["sense_guess"] else ""
        named = self._known(m)
        known = f" That matches {named}, already named." if named else ""
        note = f" ({self.note}.)" if self.note else ""
        sure = " The cycles match." if m["consistent"] else (" Turn it on and off once more to confirm." if m["cycles"] < 2 else " The cycles vary; something else may have switched.")
        return stale + f"Got it: {round(m['w'])} watts over {m['cycles']} cycle{'s' if m['cycles'] != 1 else ''}.{known}{guess}{sure} What is it?{note}"
