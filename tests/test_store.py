import json
from pathlib import Path

from senseless.collector import Collector
from senseless.store import Store

SAMPLE = Path(__file__).parent.parent / "samples" / "microwave-30s.jsonl"


def rt(frame, w=100.0, deltas=None):
    return {"frame": frame, "epoch": 1_800_000_000 + frame / 60, "w": w, "channels": [w / 2, w / 2],
            "voltage": [120, 121], "hz": 60, "deltas": deltas or [], "devices": []}


def test_replay_sample_finds_microwave(tmp_path):
    s = Store(tmp_path / "t.db")
    for line in SAMPLE.open():
        r = json.loads(line)
        if r["msg"]["type"] == "realtime_update":
            s.realtime(r["msg"]["payload"], r["t"])
    s.commit()
    big = s.db.execute("SELECT channel, round(w) FROM deltas WHERE abs(w) > 200 ORDER BY start_frame").fetchall()
    assert big == [(1, 299.0), (1, 1726.0), (1, -1907.0)]


def test_frame_reset_starts_new_generation(tmp_path):
    s = Store(tmp_path / "t.db")
    s.realtime(rt(5_000_000), 0)
    s.realtime(rt(5_000_030), 0)
    s.realtime(rt(1_000), 0)  # counter reset (monitor reboot)
    s.realtime(rt(1_030), 0)
    s.commit()
    assert s.db.execute("SELECT generation, count(*) FROM readings GROUP BY generation").fetchall() == [(0, 2), (1, 2)]
    # generation survives a restart
    s.close()
    s2 = Store(tmp_path / "t.db")
    assert s2.generation == 1 and s2.last_frame == 1_030


def test_small_reorder_is_not_a_reset(tmp_path):
    s = Store(tmp_path / "t.db")
    s.realtime(rt(5_000_000), 0)
    s.realtime(rt(4_999_970), 0)  # slightly out of order
    assert s.generation == 0


def test_malformed_messages_are_counted_not_fatal(tmp_path):
    c = Collector(tmp_path, archive=True)
    c.handle("not json", 1.0)
    c.handle(json.dumps({"type": "realtime_update", "payload": {"no_frame": 1}}), 1.0)
    c.handle(json.dumps({"type": "realtime_update", "payload": {**rt(10), "channels": [5], "voltage": None,
                                                                "devices": [{"id": "a", "w": None}],
                                                                "deltas": [{"channel": 0}]}}), 1.0)
    c.handle(json.dumps({"type": "realtime_update", "payload": rt(40, deltas=[{"channel": 1, "w": 50, "start_frame": 40, "frame": 40}])}), 2.0)
    assert c.state.messages["rejected"] == 2
    assert c.state.messages["realtime_update"] == 2
    assert c.state.deltas[("L2", "up")] == 1
    c.raw.close()
    c.store.close()


def test_watchdog_exits_when_unhealthy_too_long(tmp_path, monkeypatch):
    import asyncio
    import senseless.collector as col

    monkeypatch.setattr(col, "COMMIT_EVERY_S", 0.01)
    monkeypatch.setattr(col, "EXIT_AFTER_S", 0.05)
    exits = []

    def fake_exit(code):
        exits.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(col.os, "_exit", fake_exit)
    c = Collector(tmp_path, archive=False)  # never connects -> never healthy
    try:
        asyncio.run(asyncio.wait_for(c.watchdog(), 2))
    except SystemExit:
        pass
    assert exits == [3]
