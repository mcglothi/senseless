"""ESPI/Atom interval readings; resolve ReadingTypes through Atom resource links."""
import math
import xml.etree.ElementTree as ET

from .. import BillingError

NAME = 'greenbutton-xml'
LABEL = 'Green Button XML'
ESPI = '{http://naesb.org/espi}'
ATOM = '{http://www.w3.org/2005/Atom}'


def detect(data: bytes) -> float:
    return 0.99 if b'http://naesb.org/espi' in data and b'IntervalReading' in data else 0.0


def parse(data: bytes) -> dict:
    try:
        if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
            raise ValueError
        root = ET.fromstring(data)
        entries = root.findall('.//' + ATOM + 'entry')
        if root.tag == ATOM + 'entry':
            entries = [root]
        resources = {}
        for entry in entries:
            identity = entry.findtext(ATOM + 'id')
            if identity:
                resources[identity.rstrip('/')] = entry
            for link in entry.findall(ATOM + 'link'):
                if link.get('rel') == 'self':
                    resources[link.get('href', '').rstrip('/')] = entry
        reading_types = root.findall('.//' + ESPI + 'ReadingType')

        def linked_type(entry, seen=None):
            if entry is None:
                return None
            seen = set() if seen is None else seen
            if id(entry) in seen:
                return None
            seen.add(id(entry))
            found = entry.find('.//' + ESPI + 'ReadingType')
            if found is not None:
                return found
            for link in entry.findall(ATOM + 'link'):
                if link.get('rel') not in ('related', 'up'):
                    continue
                target = link.get('href', '').rstrip('/')
                linked = resources.get(target)
                if linked is None:
                    # Some exports mix absolute and relative resource URLs.
                    matches = [v for k, v in resources.items() if k.endswith('/' + target.lstrip('/')) or target.endswith('/' + k.lstrip('/'))]
                    linked = matches[0] if len(matches) == 1 else None
                result = linked_type(linked, seen)
                if result is not None:
                    return result
            return None

        rows = {}
        blocks = [(entry, block) for entry in entries for block in entry.findall('.//' + ESPI + 'IntervalBlock')]
        if not entries:
            blocks = [(None, block) for block in root.findall('.//' + ESPI + 'IntervalBlock')]
        for entry, block in blocks:
            rt = linked_type(entry)
            if rt is None and len(reading_types) == 1:
                rt = reading_types[0]
            if rt is None or rt.findtext(ESPI + 'uom') != '72':
                raise ValueError
            multiplier = int(rt.findtext(ESPI + 'powerOfTenMultiplier', '0'))
            if not -12 <= multiplier <= 12:
                raise ValueError
            received = rt.findtext(ESPI + 'flowDirection') == '19'
            for reading in block.findall(ESPI + 'IntervalReading'):
                ts = int(reading.findtext(ESPI + 'timePeriod/' + ESPI + 'start'))
                seconds = int(reading.findtext(ESPI + 'timePeriod/' + ESPI + 'duration'))
                kwh = float(reading.findtext(ESPI + 'value')) * 10 ** multiplier / 1000
                if not 300 <= seconds <= 86400 or not math.isfinite(kwh) or kwh < 0:
                    raise ValueError
                row = rows.setdefault((ts, seconds), [ts, seconds, 0.0, None, None])
                if received:
                    row[4] = (row[4] or 0) + kwh
                else:
                    row[2] += kwh
        if not rows:
            raise ValueError
        return {'kind': 'usage', 'rows': [tuple(r) for r in rows.values()], 'source': NAME}
    except (ET.ParseError, ValueError, TypeError, OverflowError):
        raise BillingError('invalid Green Button XML (Wh ReadingType and interval timestamps required)') from None
