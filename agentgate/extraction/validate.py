"""Business-rule validation of an extracted invoice.

These are checks an AP system can run without knowing the right answer:
arithmetic must add up, dates must be consistent, and the vendor must exist
in the vendor master with a matching name and tax rate. They catch many
extraction errors, and their messages feed the repair retry in extract.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..portals import data
from .schema import Invoice, cents

# What our AP system knows about each supplier: name, ID, tax rate in basis points.
VENDOR_MASTER = {v["vendor_id"]: {"name": v["name"], "tax_bp": v["tax_bp"]} for v in data.VENDORS.values()}


@dataclass(frozen=True)
class Issue:
    code: str
    message: str


def _name_matches(extracted: str, expected: str) -> bool:
    # "INITECH OFFICE SOLUTIONS" vs "Initech Office Solutions": compare the first word, case-insensitively.
    first = expected.split()[0].casefold()
    return first in extracted.casefold()


def validate(inv: Invoice, vendor_master: dict = VENDOR_MASTER, today: date = data.TODAY) -> list[Issue]:
    issues: list[Issue] = []

    if not inv.line_items:
        issues.append(Issue("no_line_items", "No line items were extracted."))
    for n, item in enumerate(inv.line_items, 1):
        if abs(cents(item.quantity * item.unit_price) - cents(item.amount)) > 1:
            issues.append(Issue("line_math", f"Line {n}: quantity × unit price ({item.quantity} × {item.unit_price}) "
                                             f"does not equal the line amount {item.amount}."))
    lines_total = sum(cents(i.amount) for i in inv.line_items)
    if inv.line_items and abs(lines_total - cents(inv.subtotal)) > len(inv.line_items):
        issues.append(Issue("lines_sum", f"Line items sum to {lines_total / 100:.2f} but the subtotal is "
                                         f"{inv.subtotal:.2f}."))
    if cents(inv.subtotal) + cents(inv.tax) + cents(inv.shipping) != cents(inv.total):
        issues.append(Issue("total_math", f"Subtotal {inv.subtotal:.2f} + tax {inv.tax:.2f} + shipping "
                                          f"{inv.shipping:.2f} does not equal the total {inv.total:.2f}."))
    if cents(inv.total) <= 0:
        issues.append(Issue("non_positive_total", "The total must be positive."))

    if inv.due_date < inv.invoice_date:
        issues.append(Issue("due_before_issue", f"Due date {inv.due_date} is before invoice date {inv.invoice_date}."))
    if inv.invoice_date > today:
        issues.append(Issue("future_date", f"Invoice date {inv.invoice_date} is in the future."))

    vendor = vendor_master.get(inv.vendor_id.strip())
    if vendor is None:
        issues.append(Issue("unknown_vendor_id", f"Vendor ID {inv.vendor_id!r} is not in the vendor master."))
    else:
        if not _name_matches(inv.vendor_name, vendor["name"]):
            issues.append(Issue("vendor_mismatch", f"Vendor ID {inv.vendor_id} belongs to {vendor['name']}, "
                                                   f"not {inv.vendor_name!r}."))
        expected_tax = (cents(inv.subtotal) * vendor["tax_bp"] + 5000) // 10000
        if abs(expected_tax - cents(inv.tax)) > 1:
            issues.append(Issue("tax_rate", f"Tax {inv.tax:.2f} does not match {vendor['name']}'s rate of "
                                            f"{vendor['tax_bp'] / 100:g}% on the subtotal (expected "
                                            f"{expected_tax / 100:.2f})."))
    return issues
