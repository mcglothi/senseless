"""Strip account-identifying values from a realtime capture so it can be shared.

    python tools/sanitize.py IN.jsonl[.gz] OUT.jsonl [--from HH:MM:SS --to HH:MM:SS]

IDs (monitor/user/device/guid) are replaced with stable placeholders, so
relationships inside the file survive. Power, voltage, frame and timing data
are kept: that is the point of sharing. Review the output before publishing.
"""

import argparse
import gzip
import hashlib
import json
from datetime import datetime

ID_KEYS = {"monitor_id", "user_id", "device_id", "guid", "id", "notification_id", "partner_checksum",
           "monitor_overview_checksum", "device_data_checksum"}
DROP_KEYS = {"given_location", "location", "address", "email", "serial_number", "postal_code", "city"}


def placeholder(v) -> str:
    return "x" + hashlib.sha256(str(v).encode()).hexdigest()[:8]


def clean(o):
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if k in DROP_KEYS:
                continue
            if k in ID_KEYS and v is not None and not isinstance(v, (dict, list)):
                out[k] = placeholder(v)
            elif k == "destination" and isinstance(v, str) and ":" in v:
                kind, _, val = v.partition(":")
                out[k] = f"{kind}:{placeholder(val)}"
            else:
                out[k] = clean(v)
        return out
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    a = ap.parse_args()
    n = 0
    opener = gzip.open if a.src.endswith(".gz") else open
    with opener(a.src, "rt") as f, open(a.dst, "w") as g:
        for line in f:
            r = json.loads(line)
            if "raw" in r:  # collector archive format keeps the original text
                try:
                    r["msg"] = json.loads(r.pop("raw"))
                except ValueError:
                    continue
            hms = datetime.fromtimestamp(r["t"]).strftime("%H:%M:%S")
            keep_always = r["msg"].get("type") != "realtime_update"
            if not keep_always and ((a.start and hms < a.start) or (a.end and hms > a.end)):
                continue
            g.write(json.dumps({"t": r["t"], "msg": clean(r["msg"])}) + "\n")
            n += 1
    print(f"wrote {n} messages -> {a.dst}")


if __name__ == "__main__":
    main()
