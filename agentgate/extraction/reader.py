"""Turning a PDF into model inputs: its text layer, page images, or both."""
from __future__ import annotations

import io
import shutil
import subprocess
from dataclasses import dataclass

import pypdfium2 as pdfium

from ..pdfium_lock import PDFIUM_LOCK

MIN_TEXT_CHARS = 20  # fewer characters than this means "no usable text layer" (e.g. a scan)


@dataclass
class PdfContent:
    text: str
    pages: int

    @property
    def has_text_layer(self) -> bool:
        return len(self.text.strip()) >= MIN_TEXT_CHARS


def read_text(pdf: bytes) -> PdfContent:
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf)
        try:
            texts = []
            for page in doc:
                textpage = page.get_textpage()
                texts.append(textpage.get_text_range())
                textpage.close()
                page.close()
            return PdfContent(text="\n".join(texts), pages=len(doc))
        finally:
            doc.close()


def page_images(pdf: bytes, dpi: int = 150, max_pages: int = 3) -> list[bytes]:
    """Render pages to JPEG. 150 dpi keeps small print legible without inflating image tokens."""
    images = []
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf)
        try:
            for i in range(min(len(doc), max_pages)):
                page = doc[i]
                bitmap = page.render(scale=dpi / 72)
                img = bitmap.to_pil().convert("RGB")  # convert() copies, so the bitmap can be freed
                bitmap.close()
                page.close()
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=85)
                images.append(buf.getvalue())
        finally:
            doc.close()
    return images


def ocr_text(pdf: bytes, dpi: int = 300, max_pages: int = 3) -> str:
    """OCR the rendered pages with Tesseract, for scans that have no text layer.

    300 dpi is Tesseract's sweet spot; the image goes in on stdin and the text comes back on stdout.
    """
    if shutil.which("tesseract") is None:
        raise RuntimeError("Tesseract is not installed (macOS: brew install tesseract).")
    pages = []
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf)
        try:
            for i in range(min(len(doc), max_pages)):
                page = doc[i]
                bitmap = page.render(scale=dpi / 72)
                img = bitmap.to_pil().convert("L")
                bitmap.close()
                page.close()
                buf = io.BytesIO()
                img.save(buf, "PNG")
                pages.append(buf.getvalue())
        finally:
            doc.close()
    texts = []
    for png in pages:  # outside the lock: Tesseract is a separate process
        out = subprocess.run(["tesseract", "-", "-", "--psm", "6"], input=png, capture_output=True, check=True)
        texts.append(out.stdout.decode("utf-8", "replace"))
    return "\n".join(texts)
