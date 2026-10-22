import pypdfium2 as pdfium
import pytest

from agentgate.portals import data
from agentgate.portals.friction import resolve
from agentgate.portals.pdfs import render_invoice_pdf


def pdf_text(pdf: bytes) -> str:
    return pdfium.PdfDocument(pdf)[0].get_textpage().get_text_range()


@pytest.fixture(scope="module")
def env():
    return data.generate_env("x", 3, resolve("easy"))


@pytest.mark.parametrize("vendor", list(data.VENDORS))
def test_clean_pdf_contains_every_field(env, vendor):
    inv = env.invoices[vendor][0]
    text = pdf_text(render_invoice_pdf(inv, env.seed))
    for expected in (
        inv.number, inv.po_number, data.VENDORS[vendor]["vendor_id"],
        data.fmt_date(inv.issue_date, vendor), data.fmt_date(inv.due_date, vendor),
        data.fmt_money(inv.subtotal_cents, vendor), data.fmt_money(inv.tax_cents, vendor),
        data.fmt_money(inv.amount_cents, vendor),
    ):
        assert expected in text, f"{expected!r} missing from {vendor} PDF"


def test_scanned_pdf_has_no_text_layer(env):
    inv = env.invoices["globex"][0]
    inv.scanned = True
    try:
        pdf = render_invoice_pdf(inv, env.seed)
    finally:
        inv.scanned = False
    assert pdf.startswith(b"%PDF")
    assert pdf_text(pdf).strip() == ""


@pytest.mark.parametrize("scanned", [False, True])
def test_pdfs_are_byte_for_byte_deterministic(env, scanned):
    inv = env.invoices["initech"][0]
    inv.scanned = scanned
    try:
        assert render_invoice_pdf(inv, env.seed) == render_invoice_pdf(inv, env.seed)
    finally:
        inv.scanned = False
