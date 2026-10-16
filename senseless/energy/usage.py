"""Interval persistence and overlap-aware hourly analysis in settings.db."""
import math
from bisect import bisect_right
from datetime import datetime, time, timedelta
from functools import lru_cache

from . import BillingError


@lru_cache(maxsize=1024)
def _hour_edges(day, tz):
    # Enumerating wall hours (both folds) handles fractional offsets and DST.
    return sorted({int(datetime.combine(day + timedelta(days=d), time(h), tz).replace(fold=f).timestamp())
                   for d in (-1, 0, 1) for h in range(24) for f in (0, 1)})


def interval_buckets(rows, tz=None):
    buckets = {}
    for row in rows:
        cursor, stop = row['ts'], row['ts'] + row['seconds']
        while cursor < stop:
            if tz is None:
                start = cursor // 3600 * 3600
                end = start + 3600
            else:
                edges = _hour_edges(datetime.fromtimestamp(cursor, tz).date(), tz)
                i = bisect_right(edges, cursor)
                start, end = edges[i - 1:i + 1]
            buckets.setdefault((start, end), []).append(row)
            cursor = end
    return sorted(buckets.items())


def _resolve_bucket(start, end, candidates, additive=False):
    edges = sorted({start, end} | {max(start, r['ts']) for r in candidates}
                   | {min(end, r['ts'] + r['seconds']) for r in candidates})
    for begin, stop in zip(edges, edges[1:]):
        active = [r for r in candidates if r['ts'] <= begin and r['ts'] + r['seconds'] >= stop]
        if not active:
            continue
        if not additive:
            active = [min(active, key=lambda r: (r['seconds'], -r['ts']))]
        seconds = stop - begin
        temps = [r['temp_f'] for r in active if r['temp_f'] is not None]
        exports = [r['received'] * seconds / r['seconds'] for r in active if r['received'] is not None]
        yield {'ts': begin, 'seconds': seconds, 'kwh': sum(r['kwh'] * seconds / r['seconds'] for r in active),
               'temp_f': sum(temps) / len(temps) if temps else None,
               'received': sum(exports) if exports else None}


def resolved_segments(rows, *, additive=False):
    """Resolve one stream's overlaps, or sum already resolved independent streams."""
    return [segment for (start, end), candidates in interval_buckets(rows)
            for segment in _resolve_bucket(start, end, candidates, additive)]


class UsageStore:
    def __init__(self, connect):
        self.connect = connect
        with connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS usage_interval (
                ts INTEGER NOT NULL, seconds INTEGER NOT NULL, kwh REAL NOT NULL,
                temp_f REAL, received REAL, source TEXT NOT NULL, PRIMARY KEY (ts, seconds))''')
            # INSERT OR IGNORE makes reopening safe and preserves later corrected imports.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meter_hourly'").fetchone():
                db.execute('''INSERT OR IGNORE INTO usage_interval
                    SELECT ts, 3600, kwh, temp_f, received, 'avangrid-csv' FROM meter_hourly''')

    def save(self, rows, source):
        normalized = {}
        try:
            # Importers validate source durations; overlap resolution can create
            # shorter fragments at another meter's boundaries.
            for ts, seconds, kwh, temp, received in rows:
                if type(ts) is not int or type(seconds) is not int or not 1 <= seconds <= 86400:
                    raise ValueError
                if kwh is None or any(v is not None and (type(v) not in (int, float) or not math.isfinite(v)) for v in (kwh, temp, received)):
                    raise ValueError
                normalized[ts, seconds] = (ts, seconds, kwh, temp, received, source)
            if not normalized:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise BillingError('invalid usage intervals') from None
        added = updated = 0
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for key, row in normalized.items():
                exists = db.execute('SELECT 1 FROM usage_interval WHERE ts=? AND seconds=?', key).fetchone()
                updated += bool(exists)
                added += not bool(exists)
                db.execute('''INSERT INTO usage_interval VALUES (?,?,?,?,?,?) ON CONFLICT(ts,seconds)
                    DO UPDATE SET kwh=excluded.kwh, temp_f=excluded.temp_f,
                    received=excluded.received, source=excluded.source''', row)
        return {'added': added, 'updated': updated, 'from': min(k[0] for k in normalized),
                'to': max(k[0] for k in normalized)}

    def intervals(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM usage_interval ORDER BY ts, seconds')]

    def hourly(self, tz=None):
        """Resolve overlaps and aggregate at local tariff hours (UTC by default).

        Coarser intervals fill uncovered time; energy is uniform within an interval.
        Local boundaries keep fractional-offset TOU periods and days intact.
        """
        result = []
        for (hour, end), candidates in interval_buckets(self.intervals(), tz):
            segments = list(_resolve_bucket(hour, end, candidates))
            temps = [r for r in segments if r['temp_f'] is not None]
            exports = [r['received'] for r in segments if r['received'] is not None]
            result.append({'ts': hour, 'kwh': sum(r['kwh'] for r in segments),
                           'temp_f': sum(r['temp_f'] * r['seconds'] for r in temps) / sum(r['seconds'] for r in temps) if temps else None,
                           'received': sum(exports) if exports else None,
                           'coverage_seconds': sum(r['seconds'] for r in segments)})
        return result
