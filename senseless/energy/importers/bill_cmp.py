"""CMP bill plugin; only whitelisted dates, quantities and charges leave the parser."""
import io
import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import date, datetime

from .. import BillingError
from .csv_generic import finite as _finite

NAME = "bill-cmp"
LABEL = "CMP bill (PDF or text)"

def parse_cmp_bill_text(text: str) -> dict:
    """Pure CMP text parser. Only explicitly selected dates and numbers leave this function.

    Split bills sometimes omit printed rates: infer those from the segment's rounded charge/kWh.
    Never keep meter readings, identifiers, supplier/customer text, or the original document.
    """
    number = r"\d[\d,]*(?:\.\d+)?"
    day = r"\d{2}/\d{2}/\d{4}"
    text = " ".join(text.replace("–", "-").split())

    def match(pattern, source=text):
        found = re.search(pattern, source, re.I)
        if found is None:
            raise ValueError
        return found

    def iso(value):
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()

    try:
        bill_date = iso(match(rf"Bill Date\s+({day})")[1])
        meter = match(rf"\S+\s+({day})\s+{number}\s+({day})\s+{number}\s+(\d+)\s+({number})(?![\d,.])")
        end, start, days, kwh = iso(meter[1]), iso(meter[2]), int(meter[3]), _finite(meter[4])
        if not 7 <= (date.fromisoformat(end) - date.fromisoformat(start)).days <= 62 or not 0 < kwh < 100000:
            raise ValueError
        if days != (date.fromisoformat(end) - date.fromisoformat(start)).days:
            raise ValueError
        delivery_end = match(rf"Total Current Delivery Charges\s+\+?\$\s*({number})")
        supply_end = match(rf"Total New Supplier Charges\s+\+?\$\s*({number})")
        headings = list(re.finditer(rf"Delivery Charges:\s*Residential\s*\(\s*({day})\s*-\s*({day})\s*\)", text, re.I))
        if not 1 <= len(headings) <= 2:
            raise ValueError
        segments = []
        for i, heading in enumerate(headings):
            body = text[heading.end():headings[i + 1].start() if i + 1 < len(headings) else delivery_end.start()]
            fixed = match(rf"Up to\s+({number})\s*KWH\s*(?:@\s*\$\s*({number})\s*)?\+\$\s*({number})", body)
            variable = match(rf"({number})\s*KWH\s*(?:@\s*\$\s*({number})\s*)?\+\$\s*({number})", body[fixed.end():])
            allowance, charge, used, variable_charge = map(_finite, (fixed[1], fixed[3], variable[1], variable[3]))
            rate = _finite(variable[2]) if variable[2] else variable_charge / used if used else 0.0
            segment_start, segment_end = iso(heading[1]), iso(heading[2])
            if segment_start > segment_end or not start <= segment_start <= segment_end <= end:
                raise ValueError
            segments.append({"start": segment_start, "end": segment_end, "kwh": allowance + used,
                             "fixed_kwh": allowance, "fixed": charge, "delivery_rate": rate, "delivery_amount": variable_charge})
        if abs(sum(s["kwh"] for s in segments) - kwh) > 0.01:
            raise ValueError
        # supply is one line, or one per segment when the supply price changed mid-cycle (each January)
        energies = list(re.finditer(rf"Energy Charge\s+({number})\s*KWH\s*@\s*\$\s*({number})\s*\+\$\s*({number})", text, re.I))
        if not 1 <= len(energies) <= 2 or abs(sum(_finite(e[1]) for e in energies) - kwh) > 0.01:
            raise ValueError
        energy = energies[-1]
        supply_rate = (energies[0][2] and _finite(energies[0][2])) if len(energies) == 1 else \
            sum(_finite(e[3]) for e in energies) / kwh        # kWh-weighted, so kWh x rate = the energy charges

        def tax(body):
            found = re.search(rf"Maine Sales Tax\s+\+?\$\s*({number})", body, re.I)
            return _finite(found[1]) if found else 0.0

        delivery_total, supply_total = _finite(delivery_end[1]), _finite(supply_end[1])
        bill = {"start": start, "end": end, "kwh": kwh, "amount": round(delivery_total + supply_total, 2),
                "bill_date": bill_date, "delivery_total": delivery_total, "supply_total": supply_total,
                "delivery_tax": tax(text[headings[0].start():delivery_end.start()]),
                "supply_tax": tax(text[energy.end():supply_end.start()]),
                "fixed_total": round(sum(s["fixed"] for s in segments), 2), "supply_rate": round(supply_rate, 6),
                "segments_json": json.dumps(segments), "source": NAME}
        bill["lines"] = []
        for segment in segments:
            common = {"start": segment["start"], "end": segment["end"]}
            bill["lines"].extend([
                {"label": "Fixed delivery", "kind": "fixed", "amount": segment["fixed"], **common},
                {"label": "Delivery energy", "kind": "delivery", "kwh": segment["kwh"] - segment["fixed_kwh"],
                 "rate": segment["delivery_rate"],
                 "amount": segment["delivery_amount"], **common}])
        bill["lines"].extend({"label": "Supply energy", "kind": "supply", "kwh": _finite(e[1]),
                              "rate": _finite(e[2]), "amount": _finite(e[3])} for e in energies)
        bill["lines"].extend([
            {"label": "Delivery tax", "kind": "tax", "amount": bill["delivery_tax"]},
            {"label": "Supply tax", "kind": "tax", "amount": bill["supply_tax"]}])
        return bill
    except (ValueError, TypeError, OverflowError, ZeroDivisionError):
        raise BillingError("unsupported or incomplete CMP bill") from None


# pypdf diagnostics may quote document bytes. Silence them only in the importing context,
# leaving other threads' logging intact. Exceptions are also replaced with a constant error.
_private_pdf = ContextVar("private_billing_pdf", default=False)


class _PDFPrivacyFilter(logging.Filter):
    def filter(self, record):
        return not _private_pdf.get()


def parse_cmp_bill_pdf(data: bytes) -> dict:
    from pypdf import PdfReader

    # pypdf uses module loggers; filters on the parent logger do not filter descendants.
    # Some modules create their logger only on the first diagnostic, so include
    # imported modules as well as already-created loggers.
    for name in set(sys.modules) | set(logging.Logger.manager.loggerDict):
        if name == "pypdf" or name.startswith("pypdf."):
            logger = logging.getLogger(name)
            if not any(isinstance(f, _PDFPrivacyFilter) for f in logger.filters):
                logger.addFilter(_PDFPrivacyFilter())
    token = _private_pdf.set(True)
    try:
        reader = PdfReader(io.BytesIO(data))
        return parse_cmp_bill_text("\n".join(page.extract_text() or "" for page in reader.pages))
    except Exception:
        raise BillingError("unsupported or unreadable CMP PDF") from None
    finally:
        _private_pdf.reset(token)


def check(bill: dict) -> dict | None:
    """Price a historical bill with its own fixed charges, allowances, and rates."""
    if not bill.get("segments_json") or bill.get("supply_rate") is None:
        return None
    segments = json.loads(bill["segments_json"])
    kwh = bill["kwh"]
    taxable = max(0.0, kwh - 750)
    delivery = sum(s["fixed"] + max(0.0, s["kwh"] - s["fixed_kwh"]) * s["delivery_rate"] for s in segments)
    delivery_tax = round(sum(taxable * s["kwh"] / kwh * s["delivery_rate"] * 0.055 for s in segments), 2)
    supply = round(kwh * bill["supply_rate"], 2)
    supply_tax = round(taxable * bill["supply_rate"] * 0.055, 2)
    total = round(round(delivery, 2) + delivery_tax + supply + supply_tax, 2)
    return {"model_total": total, "diff": round(bill["amount"] - total, 2)}



def detect(data: bytes) -> float:
    if data.startswith(b"%PDF-"):
        try:
            parse_cmp_bill_pdf(data)
            return 1.0
        except BillingError:
            return 0.6  # PDF recognized; parsing will give a private, useful error.
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        return 0.0
    return 0.95 if "Delivery Charges:" in text and "Total New Supplier Charges" in text else 0.0


def parse(data: bytes) -> dict:
    if data.startswith(b"%PDF-"):
        bill = parse_cmp_bill_pdf(data)
    else:
        try:
            bill = parse_cmp_bill_text(data.decode("utf-8-sig"))
        except UnicodeError:
            raise BillingError("unsupported CMP bill text") from None
    return {"kind": "bill", "bill": bill}
