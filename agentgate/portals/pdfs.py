"""Synthetic invoice PDFs.

Each vendor has its own template, the way real suppliers do:
  classic   – US layout, labeled meta block, "TOTAL DUE"
  european  – bilingual German/English, DD.MM.YYYY, 1.234,56 amounts, VAT
  statement – monospaced dot-leader statement with a "previous balance" distractor

A "scanned" PDF is the clean PDF rasterized, rotated, noised, blurred and
JPEG-compressed, then wrapped back into a PDF. It has no text layer, so
reading it requires vision/OCR, which is what makes it hard.

Output is byte-for-byte deterministic for a given invoice and seed.
"""
from __future__ import annotations

import io
import random

import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageEnhance, ImageFilter
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from .data import BILL_TO, VENDORS, Invoice, fmt_date, fmt_money

W, H = letter  # 612 x 792 points; (0, 0) is the bottom-left corner
SCAN_DPI = 150


def render_invoice_pdf(inv: Invoice, seed: int) -> bytes:
    clean = _render_clean(inv)
    if not inv.scanned:
        return clean
    return _scanify(clean, random.Random(f"{seed}:scanfx:{inv.number}"))


def _render_clean(inv: Invoice) -> bytes:
    buf = io.BytesIO()
    # invariant=1 drops the creation timestamp and random document ID from the output.
    c = Canvas(buf, pagesize=letter, invariant=1)
    c.setTitle(f"Invoice {inv.number}")
    c.setAuthor(VENDORS[inv.vendor]["name"])
    TEMPLATES[VENDORS[inv.vendor]["pdf_template"]](c, inv)
    c.showPage()
    c.save()
    return buf.getvalue()


# --- shared drawing helpers --------------------------------------------------

def _money(inv: Invoice, cents: int) -> str:
    return fmt_money(cents, inv.vendor)


def _line_items(c: Canvas, inv: Invoice, top: float, headers: list[str], xs: list[float],
                font: str = "Helvetica", numbered: bool = False) -> float:
    """Draw the line-item table. xs[0] is left-aligned, the rest right-aligned. Returns the y below it."""
    c.setFont(f"{font}-Bold" if font == "Helvetica" else font, 9)
    for i, (header, x) in enumerate(zip(headers, xs)):
        (c.drawString if i == 0 else c.drawRightString)(x, top, header)
    c.setLineWidth(0.6)
    c.line(xs[0], top - 5, xs[-1], top - 5)
    c.setFont(font, 9)
    y = top - 20
    for n, line in enumerate(inv.lines, 1):
        description = f"{n}  {line['description']}" if numbered else line["description"]
        cells = [description, str(line["qty"]), _money(inv, line["unit_cents"]),
                 _money(inv, line["qty"] * line["unit_cents"])]
        for i, (cell, x) in enumerate(zip(cells, xs)):
            (c.drawString if i == 0 else c.drawRightString)(x, y, cell)
        y -= 15
    c.line(xs[0], y + 8, xs[-1], y + 8)
    return y


def _rows(c: Canvas, x_label: float, x_value: float, y: float, rows: list[tuple[str, str]],
          font: str = "Helvetica", size: int = 9, bold_last: bool = False, step: int = 14) -> float:
    for i, (label, value) in enumerate(rows):
        is_bold = bold_last and i == len(rows) - 1
        c.setFont(f"{font}-Bold" if is_bold else font, size + (1 if is_bold else 0))
        c.drawString(x_label, y, label)
        c.drawRightString(x_value, y, value)
        y -= step
    return y


# --- templates ---------------------------------------------------------------

def _classic(c: Canvas, inv: Invoice) -> None:
    v = VENDORS[inv.vendor]
    c.setFillColor(HexColor(v["color"]))
    c.setFont("Helvetica-Bold", 20)
    c.drawString(50, H - 60, v["name"])
    c.setFillColor(black)
    c.setFont("Helvetica", 9)
    for i, line in enumerate(v["address"]):
        c.drawString(50, H - 78 - i * 12, line)

    c.setFont("Helvetica-Bold", 26)
    c.drawRightString(W - 50, H - 60, "INVOICE")
    _rows(c, W - 250, W - 50, H - 90, [
        ("Invoice #", inv.number),
        ("Invoice Date", fmt_date(inv.issue_date, inv.vendor)),
        ("Due Date", fmt_date(inv.due_date, inv.vendor)),
        ("Terms", f"Net {inv.terms_days}"),
        ("PO Number", inv.po_number),
        ("Customer No.", v["customer_no"]),
        ("Vendor ID", v["vendor_id"]),
    ], step=13)

    c.setFont("Helvetica-Bold", 9)
    c.drawString(50, H - 190, "BILL TO")
    c.setFont("Helvetica", 9)
    for i, line in enumerate(BILL_TO):
        c.drawString(50, H - 204 - i * 12, line)

    y = _line_items(c, inv, H - 290, ["Description", "Qty", "Unit Price", "Amount"], [50, 360, 460, W - 50])
    _rows(c, W - 250, W - 50, y - 15, [
        ("Subtotal", _money(inv, inv.subtotal_cents)),
        (v["tax_label"], _money(inv, inv.tax_cents)),
        ("Shipping", _money(inv, inv.shipping_cents)),
        ("TOTAL DUE", _money(inv, inv.amount_cents)),
    ], bold_last=True)

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(50, 60, f"Please reference invoice {inv.number} with your payment. "
                         f"Late payments may incur a 1.5% monthly service charge.")


def _european(c: Canvas, inv: Invoice) -> None:
    v = VENDORS[inv.vendor]
    c.setFillColor(HexColor(v["color"]))
    c.rect(0, H - 90, W, 90, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, H - 45, v["name"])
    c.setFont("Helvetica", 9)
    c.drawString(40, H - 62, " · ".join(v["address"]))
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(W - 40, H - 45, "RECHNUNG / INVOICE")
    c.setFillColor(black)

    # Four boxed meta fields across the page.
    boxes = [
        ("Rechnungs-Nr. / Invoice no.", inv.number),
        ("Datum / Date", fmt_date(inv.issue_date, inv.vendor)),
        ("Fällig am / Due", fmt_date(inv.due_date, inv.vendor)),
        ("Ihre Bestellung / Your PO", inv.po_number),
    ]
    box_w = (W - 80) / 4
    for i, (label, value) in enumerate(boxes):
        x = 40 + i * box_w
        c.rect(x, H - 150, box_w - 6, 42, fill=0, stroke=1)
        c.setFont("Helvetica", 7)
        c.drawString(x + 6, H - 120, label)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x + 6, H - 140, value)

    c.setFont("Helvetica", 8)
    c.drawString(40, H - 170, f"Lieferanten-ID / Vendor ID: {v['vendor_id']}    "
                              f"Kundennummer / Customer no.: {v['customer_no']}    USt-IdNr.: DE 291 447 830")
    c.setFont("Helvetica", 9)
    for i, line in enumerate(BILL_TO):
        c.drawString(W - 220, H - 200 - i * 12, line)

    y = _line_items(c, inv, H - 270, ["Pos. Beschreibung / Description", "Menge / Qty", "Einzelpreis / Unit",
                                       "Gesamt / Total"], [40, 330, 450, W - 40], numbered=True)
    _rows(c, W - 260, W - 40, y - 15, [
        ("Zwischensumme / Subtotal", _money(inv, inv.subtotal_cents)),
        (v["tax_label"], _money(inv, inv.tax_cents)),
        ("Versand / Shipping", _money(inv, inv.shipping_cents)),
        ("Gesamtbetrag / Total", _money(inv, inv.amount_cents)),
    ], bold_last=True)

    c.setFont("Helvetica", 7)
    c.drawString(40, 70, f"Zahlbar innerhalb von {inv.terms_days} Tagen ohne Abzug. / "
                         f"Payable within {inv.terms_days} days without deduction.")
    c.drawString(40, 58, "Bank: Hanseatic Test Bank · IBAN DE00 0000 0000 0000 0000 00 · BIC TESTDEHHXXX")


def _statement(c: Canvas, inv: Invoice) -> None:
    v = VENDORS[inv.vendor]
    font = "Courier"
    c.setFont("Courier-Bold", 14)
    c.drawString(50, H - 55, v["name"].upper())
    c.setFont(font, 9)
    for i, line in enumerate(v["address"]):
        c.drawString(50, H - 72 - i * 11, line)
    c.setFont("Courier-Bold", 12)
    c.drawRightString(W - 50, H - 55, "STATEMENT OF CHARGES")
    c.setFont(font, 9)
    c.drawString(50, H - 115, "-" * 86)

    def leader(label: str) -> str:
        return f"{label} ".ljust(22, ".") + " "

    meta = [
        ("DOCUMENT NO", inv.number),
        ("DOCUMENT DATE", fmt_date(inv.issue_date, inv.vendor)),
        ("ACCOUNT", v["customer_no"]),
        ("SUPPLIER REF", v["vendor_id"]),
        ("CUSTOMER PO", inv.po_number),
        ("TERMS", f"NET {inv.terms_days}"),
        ("PAYMENT DUE", fmt_date(inv.due_date, inv.vendor)),
    ]
    for i, (label, value) in enumerate(meta):
        c.drawString(50, H - 135 - i * 13, leader(label) + value)

    c.drawString(340, H - 135, "SOLD TO:")
    for i, line in enumerate(BILL_TO):
        c.drawString(340, H - 148 - i * 11, line)

    y = _line_items(c, inv, H - 255, ["ITEM", "QTY", "EACH", "EXTENDED"], [50, 340, 450, W - 50], font=font)
    _rows(c, 300, W - 50, y - 15, [
        ("PREVIOUS BALANCE", _money(inv, 0)),
        ("MERCHANDISE", _money(inv, inv.subtotal_cents)),
        (v["tax_label"], _money(inv, inv.tax_cents)),
        ("FREIGHT", _money(inv, inv.shipping_cents)),
        ("BALANCE DUE", _money(inv, inv.amount_cents)),
    ], font=font, bold_last=True)
    c.setFont(font, 8)
    c.drawString(50, 60, "QUESTIONS ABOUT THIS STATEMENT? CONTACT ACCOUNTS RECEIVABLE.")


TEMPLATES = {"classic": _classic, "european": _european, "statement": _statement}


# --- scanned variant ---------------------------------------------------------

def _scanify(pdf_bytes: bytes, rng: random.Random) -> bytes:
    page = pdfium.PdfDocument(pdf_bytes)[0]
    img = page.render(scale=SCAN_DPI / 72).to_pil().convert("L")  # 72 points per inch

    # A slightly crooked page.
    img = img.rotate(rng.uniform(-1.5, 1.5), resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)

    # Sensor grain. Built from the seeded rng (PIL's effect_noise isn't seedable),
    # then squeezed around 128 so ImageChops.add with offset=-128 adds zero-mean noise.
    amplitude = rng.uniform(0.10, 0.18)
    noise = Image.frombytes("L", img.size, rng.randbytes(img.size[0] * img.size[1]))
    noise = noise.point(lambda p: int(128 + (p - 128) * amplitude))
    img = ImageChops.add(img, noise, scale=1.0, offset=-128)

    # Soft focus, washed-out toner, heavy compression.
    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 0.8)))
    img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.7, 0.88))
    jpeg = io.BytesIO()
    img.save(jpeg, "JPEG", quality=rng.randint(30, 45))

    # Wrap the JPEG in a one-page PDF: an image and nothing else, no text layer.
    out = io.BytesIO()
    c = Canvas(out, pagesize=letter, invariant=1)
    jpeg.seek(0)
    c.drawImage(ImageReader(jpeg), 0, 0, width=W, height=H)
    c.showPage()
    c.save()
    return out.getvalue()
