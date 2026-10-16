"""Header-driven CSVs and saved mappings. Uploaded identity values are never retained.

Smart Meter Texas and PG&E-style profiles follow published formats and are NOT yet
verified against real exports. Ambiguous local DST times are rejected; use an offset
or a saved profile with an unambiguous timestamp for those rows.
"""
import csv
import io
import math
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ...summary import local_tz
from .. import BillingError

NAME = 'csv-generic'
LABEL = 'Meter usage CSV'
AVANGRID = 'Name,Address,Account Number,Service,Type,Date,Start Time,End Time,Net,Units,Costs,Weather,Delivered,Received'.split(',')
SMT = 'ESIID,USAGE_DATE,REVISION_DATE,USAGE_START_TIME,USAGE_END_TIME,USAGE_KWH,ESTIMATED_ACTUAL,CONSUMPTION_SURPLUSGENERATION'.split(',')
GREENBUTTON = 'TYPE,DATE,START TIME,END TIME,USAGE,UNITS,COST,NOTES'.split(',')
IDENTITY = re.compile(r'name|address|account|esiid|meter|service|customer|invoice|serial|location', re.I)
SAFE_HEADER = re.compile(r'^(?:type|date|start(?:[ _]time)?|end(?:[ _]time)?|time|timestamp|usage|usage_kwh|kwh|wh|units|costs?|notes|net|weather|temp(?:erature)?(?:_f)?|delivered|received|usage_date|revision_date|usage_start_time|usage_end_time|estimated_actual|consumption_surplusgeneration)$', re.I)


def finite(value):
    number = float(str(value).replace(',', ''))
    if not math.isfinite(number):
        raise ValueError
    return number


def headers(data):
    try:
        return next(csv.reader(io.StringIO(data.decode('utf-8-sig')), strict=True))
    except (UnicodeError, csv.Error, StopIteration):
        return []


def safe_header(data):
    # Unknown column labels can themselves contain household data: only return known labels.
    return [h.strip() if not IDENTITY.search(h) and SAFE_HEADER.fullmatch(h.strip()) else '[redacted]'
            for h in headers(data)][:100]


def normalized(names):
    return [h.strip().casefold() for h in names]


def profile_for(data, profiles=()):
    found = normalized(headers(data))
    for name, names in [('avangrid-csv', AVANGRID), ('smart-meter-texas', SMT), ('greenbutton-csv', GREENBUTTON)]:
        if set(found) == set(normalized(names)) and len(found) == len(names):
            return name, None
    for profile in profiles:
        if set(found) == set(normalized(profile['match_headers'])) and len(found) == len(profile['match_headers']):
            return 'saved-csv', profile
    return None, None


def detect(data: bytes) -> float:
    return 0.9 if profile_for(data)[0] else 0.0


def validate_profile(value):
    """Validate schema only; never quote a user-provided column or value in an error."""
    try:
        if not isinstance(value, dict) or set(value) - {'name', 'match_headers', 'columns', 'time_format', 'timezone', 'units'}:
            raise ValueError
        if not isinstance(value['name'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', value['name']):
            raise ValueError
        names = value['match_headers']
        if not isinstance(names, list) or not names or len(names) > 100 or any(not isinstance(h, str) or not 0 < len(h) <= 100 for h in names):
            raise ValueError
        if len(set(normalized(names))) != len(names):
            raise ValueError
        columns = value['columns']
        if not isinstance(columns, dict) or set(columns) - {'start', 'end', 'date', 'time', 'kwh', 'temp_f'}:
            raise ValueError
        if 'kwh' not in columns or not ('start' in columns or {'date', 'time'} <= columns.keys()):
            raise ValueError
        if any(not isinstance(h, str) or h.casefold().strip() not in normalized(names) or IDENTITY.search(h) for h in columns.values()):
            raise ValueError
        if value.get('units', 'kWh') not in ('kWh', 'Wh'):
            raise ValueError
        if 'timezone' in value:
            ZoneInfo(value['timezone'])
        if 'time_format' in value and (not isinstance(value['time_format'], str) or len(value['time_format']) > 100):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise BillingError('invalid CSV mapping (name must be a short slug; map only usage/time/temperature columns)') from None
    return {**value, 'units': value.get('units', 'kWh')}


def timestamp(value, tz, fmt=None):
    # SMT exports may spell the final interval's end as 24:00.
    if not fmt and re.search(r' 24:00(?::00)?$', value.strip()):
        base = timestamp(re.sub(r' 24:00(?::00)?$', ' 00:00', value.strip()), tz)
        next_day = datetime.fromtimestamp(base, tz).date() + timedelta(days=1)
        return timestamp(next_day.isoformat() + ' 00:00', tz)
    if fmt:
        dt = datetime.strptime(value.strip(), fmt)
    else:
        try:
            dt = datetime.fromisoformat(value.strip())
        except ValueError:
            dt = next((parsed for pattern in ('%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M', '%m/%d/%Y %I:%M %p')
                       if (parsed := _try_date(value.strip(), pattern)) is not None), None)
            if dt is None:
                raise ValueError
    if dt.utcoffset() is None:
        early, late = dt.replace(tzinfo=tz, fold=0), dt.replace(tzinfo=tz, fold=1)
        if early.utcoffset() != late.utcoffset():
            raise ValueError  # gap or repeated local hour: never silently guess
        dt = early
    return int(dt.timestamp())


def _try_date(value, pattern):
    try:
        return datetime.strptime(value, pattern)
    except ValueError:
        return None


def parse(data: bytes, profiles=()) -> dict:
    name, profile = profile_for(data, profiles)
    try:
        if not name:
            raise ValueError
        reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig')), strict=True)
        reader.fieldnames = normalized(reader.fieldnames)
        rows = {}
        smt_channels = {}
        for raw in reader:
            if None in raw or any(v is None for v in raw.values()):
                raise ValueError
            # Only these selected fields leave the current iteration.
            row = {k: v.strip() for k, v in raw.items()}
            temp = received = None
            if name == 'avangrid-csv':
                if row['units'].casefold() != 'kwh':
                    raise ValueError
                start = datetime.fromisoformat(row['start time'])
                if start.utcoffset() is None or start.minute or start.second or start.microsecond:
                    raise ValueError
                ts, seconds = int(start.timestamp()), 3600
                kwh = finite(row['net'])
                temp = finite(row['weather']) if row['weather'] else None
                received = finite(row['received']) if row['received'] else None
            elif name in ('smart-meter-texas', 'greenbutton-csv'):
                smt = name == 'smart-meter-texas'
                tz = ZoneInfo('America/Chicago') if smt else local_tz()
                day = row['usage_date' if smt else 'date']
                ts = timestamp(day + ' ' + row['usage_start_time' if smt else 'start time'], tz)
                end = timestamp(day + ' ' + row['usage_end_time' if smt else 'end time'], tz)
                if end <= ts:
                    # A midnight end is the following local calendar day (DST aware).
                    next_day = datetime.fromtimestamp(ts, tz).date() + timedelta(days=1)
                    end = timestamp(next_day.isoformat() + ' ' + row['usage_end_time' if smt else 'end time'], tz)
                seconds = end - ts
                kwh = finite(row['usage_kwh' if smt else 'usage'])
                if not smt:
                    if row['units'].casefold() not in ('kwh', 'wh'):
                        raise ValueError
                    kwh /= 1000 if row['units'].casefold() == 'wh' else 1
                if smt:
                    if seconds != 900:
                        raise ValueError
                    direction = row['consumption_surplusgeneration'].casefold().replace(' ', '')
                    if direction not in ('consumption', 'surplusgeneration'):
                        raise ValueError
                    if direction == 'surplusgeneration':
                        received, kwh = kwh, 0.0
            else:
                columns = {k: v.strip().casefold() for k, v in profile['columns'].items()}
                tz = ZoneInfo(profile['timezone']) if profile.get('timezone') else local_tz()
                value = row[columns['start']] if 'start' in columns else row[columns['date']] + ' ' + row[columns['time']]
                ts = timestamp(value, tz, profile.get('time_format'))
                seconds = 3600
                if 'end' in columns:
                    value = row[columns['end']]
                    if 'date' in columns and not re.search(r'\d{4}', value):
                        value = row[columns['date']] + ' ' + value
                    seconds = timestamp(value, tz, profile.get('time_format')) - ts
                kwh = finite(row[columns['kwh']]) / (1000 if profile['units'] == 'Wh' else 1)
                temp = finite(row[columns['temp_f']]) if columns.get('temp_f') and row[columns['temp_f']] else None
            if not 300 <= seconds <= 86400:
                raise ValueError
            key = (ts, seconds)
            if key in rows and name == 'smart-meter-texas':
                # Keep the final revision of each flow instead of adding duplicate consumption.
                smt_channels[key, direction] = (kwh, received)
                used = smt_channels.get((key, 'consumption'), (0, None))[0]
                exported = smt_channels.get((key, 'surplusgeneration'), (0, None))[1]
                rows[key] = (ts, seconds, used, temp, exported)
            else:
                if name == 'smart-meter-texas':
                    smt_channels[key, direction] = (kwh, received)
                rows[key] = (ts, seconds, kwh, temp, received)
        if not rows:
            raise ValueError
        return {'kind': 'usage', 'rows': list(rows.values()), 'source': NAME}
    except (ValueError, TypeError, KeyError, OverflowError, UnicodeError, csv.Error):
        raise BillingError('invalid usage CSV (check timestamps, interval lengths, mapping and units)') from None
