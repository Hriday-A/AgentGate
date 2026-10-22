"""Turning a PDF into model inputs: its text layer, page images, or both."""
from __future__ import annotations

import io
from dataclasses import dataclass

import pypdfium2 as pdfium

MIN_TEXT_CHARS = 20  # fewer characters than this means "no usable text layer" (e.g. a scan)


@dataclass
class PdfContent:
    text: str
    pages: int

    @property
    def has_text_layer(self) -> bool:
        return len(self.text.strip()) >= MIN_TEXT_CHARS


def read_text(pdf: bytes) -> PdfContent:
    doc = pdfium.PdfDocument(pdf)
    text = "\n".join(page.get_textpage().get_text_range() for page in doc)
    return PdfContent(text=text, pages=len(doc))


def page_images(pdf: bytes, dpi: int = 150, max_pages: int = 3) -> list[bytes]:
    """Render pages to JPEG. 150 dpi keeps small print legible without inflating image tokens."""
    images = []
    doc = pdfium.PdfDocument(pdf)
    for i in range(min(len(doc), max_pages)):
        img = doc[i].render(scale=dpi / 72).to_pil().convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        images.append(buf.getvalue())
    return images
