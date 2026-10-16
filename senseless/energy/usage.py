"""Interval persistence and overlap-aware hourly analysis in settings.db."""
import math

from . import BillingError


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
            for ts, seconds, kwh, temp, received in rows:
                if type(ts) is not int or type(seconds) is not int or not 300 <= seconds <= 86400:
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

    def hourly(self):
        """Choose the finest observation for every covered segment, then sum by UTC hour.

        Coarser intervals fill only uncovered time. Energy is uniform within an interval;
        this also supports intervals crossing hours and partial finer-interval coverage.
        """
        rows = self.intervals()
        buckets = {}
        for row in rows:
            stop = row['ts'] + row['seconds']
            hour = row['ts'] // 3600 * 3600
            while hour < stop:
                buckets.setdefault(hour, []).append(row)
                hour += 3600
        result = []
        for hour, candidates in sorted(buckets.items()):
            edges = sorted({hour, hour + 3600} | {max(hour, r['ts']) for r in candidates}
                           | {min(hour + 3600, r['ts'] + r['seconds']) for r in candidates})
            kwh = received = temp_sum = temp_seconds = covered = 0
            has_received = False
            for start, end in zip(edges, edges[1:]):
                active = [r for r in candidates if r['ts'] <= start and r['ts'] + r['seconds'] >= end]
                if not active:
                    continue
                row = min(active, key=lambda r: (r['seconds'], -r['ts']))
                seconds = end - start
                covered += seconds
                kwh += row['kwh'] * seconds / row['seconds']
                if row['received'] is not None:
                    received += row['received'] * seconds / row['seconds']
                    has_received = True
                if row['temp_f'] is not None:
                    temp_sum += row['temp_f'] * seconds
                    temp_seconds += seconds
            result.append({'ts': hour, 'kwh': kwh, 'temp_f': temp_sum / temp_seconds if temp_seconds else None,
                           'received': received if has_received else None, 'coverage_seconds': covered})
        return result
