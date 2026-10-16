"""ESPI/Atom interval readings; resolve ReadingTypes through Atom resource links."""
import math
import xml.etree.ElementTree as ET

from .. import BillingError
from ..usage import resolved_segments

NAME = 'greenbutton-xml'
LABEL = 'Green Button XML'
ESPI = '{http://naesb.org/espi}'
ATOM = '{http://www.w3.org/2005/Atom}'
MAX_XML_TEXT = 20 * 1024 * 1024


class _NoDTDTreeBuilder(ET.TreeBuilder):
    """Reject DTDs before Expat expands any entity, regardless of XML encoding.

    Entity expansion budget is zero. Bound ordinary text as well, including when
    this importer is called directly rather than through the upload size guard.
    """
    def __init__(self):
        super().__init__()
        self.text_size = 0

    def doctype(self, name, pubid, system):
        raise ValueError('DTD forbidden')

    def data(self, text):
        self.text_size += len(text)
        if self.text_size > MAX_XML_TEXT:
            raise ValueError('XML text limit')
        super().data(text)


def detect(data: bytes) -> float:
    return 0.99 if b'http://naesb.org/espi' in data and b'IntervalReading' in data else 0.0


def parse(data: bytes) -> dict:
    try:
        root = ET.fromstring(data, parser=ET.XMLParser(target=_NoDTDTreeBuilder()))
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

        def usage_point(entry, seen=None):
            # Resource paths and Atom links identify a stream only during parsing.
            # No UsagePoint/MeterReading identity is returned or persisted.
            if entry is None:
                return ''
            seen = set() if seen is None else seen
            if id(entry) in seen:
                return ''
            seen.add(id(entry))
            if entry.find('.//' + ESPI + 'UsagePoint') is not None:
                return entry.findtext(ATOM + 'id') or str(id(entry))
            targets = [entry.findtext(ATOM + 'id', '')] + [l.get('href', '') for l in entry.findall(ATOM + 'link')]
            for target in targets:
                path = target.rstrip('/')
                if 'UsagePoint/' in path:
                    return 'UsagePoint/' + path.split('UsagePoint/', 1)[1].split('/')[0]
            for link in entry.findall(ATOM + 'link'):
                if link.get('rel') in ('up', 'related'):
                    result = usage_point(resources.get(link.get('href', '').rstrip('/')), seen)
                    if result:
                        return result
            return ''

        streams = {}
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
            rows = streams.setdefault((usage_point(entry), received), {})
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
        if not streams:
            raise ValueError
        resolved = []
        for rows in streams.values():
            resolved.extend(resolved_segments([
                dict(zip(('ts', 'seconds', 'kwh', 'temp_f', 'received'), r)) for r in rows.values()]))
        combined = resolved_segments(resolved, additive=True)
        if not combined:
            raise ValueError
        return {'kind': 'usage', 'rows': [(r['ts'], r['seconds'], r['kwh'], r['temp_f'], r['received'])
                                         for r in combined], 'source': NAME}
    except (ET.ParseError, ValueError, TypeError, OverflowError):
        raise BillingError('invalid Green Button XML (Wh ReadingType and interval timestamps required)') from None
