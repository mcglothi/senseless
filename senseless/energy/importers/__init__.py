"""In-repo importers only. Detection never uses filenames or uploaded MIME types."""
import json

from .. import BillingError
from . import bill_cmp, csv_generic, greenbutton_xml

IMPORTERS = (greenbutton_xml, csv_generic, bill_cmp)
LABELS = {'avangrid-csv': 'Avangrid / CMP usage CSV', 'smart-meter-texas': 'Smart Meter Texas CSV',
          'greenbutton-csv': 'Green Button CSV', 'saved-csv': 'Saved CSV mapping'}


def detect(data: bytes, profiles=()):
    candidates = [(module.detect(data), module) for module in IMPORTERS]
    if csv_generic.profile_for(data, profiles)[0]:
        candidates.append((0.95, csv_generic))
    confidence, module = max(candidates, key=lambda item: item[0])
    return (module if confidence > 0.5 else None), confidence


def import_data(data: bytes, profiles=()):
    module, confidence = detect(data, profiles)
    if module is None:
        header = csv_generic.safe_header(data)
        raise BillingError('unrecognized upload; supported: Green Button XML, usage CSV, CMP bill; '
                           'CSV header: ' + json.dumps(header))
    result = module.parse(data, profiles) if module is csv_generic else module.parse(data)
    # Registry modules have a stable NAME. The source further identifies CSV format.
    result.update(importer=module.NAME, label=LABELS.get(csv_generic.profile_for(data, profiles)[0], module.LABEL) if module is csv_generic else module.LABEL, confidence=confidence)
    return result


def preview(data: bytes, profiles=()):
    module, confidence = detect(data, profiles)
    if module is None:
        return {'importer': None, 'label': None, 'confidence': confidence, 'kind': None,
                'sample': [], 'header': csv_generic.safe_header(data), 'header_hash': csv_generic.header_hash(data)}
    result = import_data(data, profiles)
    sample = result['rows'][:5] if result['kind'] == 'usage' else [result['bill']]
    return {k: result[k] for k in ('importer', 'label', 'confidence', 'kind')} | {'sample': sample, 'header': []}


def check_bill(bill):
    """Optional own-rates check supplied by the bill's in-repo importer."""
    module = next((m for m in IMPORTERS if m.NAME == bill.get('source')), None)
    return module.check(bill) if module is not None and hasattr(module, 'check') else None
