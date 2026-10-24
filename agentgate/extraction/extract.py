"""PDF → validated Invoice, using any backend from agentgate.llm.

Loop, up to `max_attempts` model calls:
  1. Ask for JSON matching the Invoice schema (text layer and/or page images).
  2. Parse it with Pydantic. On a schema error, retry with the error message.
  3. Run business-rule validation. If `repair` is on and checks fail, retry
     with the failed checks as feedback ("validation-guided repair").
The attempt with the fewest validation issues wins.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic import ValidationError

from .reader import ocr_text, page_images, read_text
from .schema import Invoice, InvoiceText, llm_schema
from .validate import Issue, validate

Mode = Literal["auto", "text", "image", "both"]
Scans = Literal["image", "ocr"]      # how to read a PDF without a text layer
Amounts = Literal["number", "text"]  # model outputs numbers, or copies printed text that code parses

SYSTEM_PROMPT = """You are an accounts-payable specialist extracting data from vendor invoices.

Rules:
- Dates: output YYYY-MM-DD. Read the document's own convention: 06.09.2026 is 6 September 2026 (day first),
  05/23/2026 is 23 May 2026 (month first).
- Amounts: plain numbers, no currency symbols or thousands separators. "1.234,56" means 1234.56.
- total is the amount payable for this invoice only. Ignore previous balances, payments and credits.
- vendor_id is the supplier's own vendor ID. Do not use the customer/account number or a tax ID.
- Copy invoice numbers and PO numbers exactly, including dashes and letter suffixes.
- Include every line item."""

SCHEMA = llm_schema(Invoice)
TEXT_SCHEMA = llm_schema(InvoiceText)
TEXT_AMOUNTS_PROMPT = SYSTEM_PROMPT.replace(
    '- Amounts: plain numbers, no currency symbols or thousands separators. "1.234,56" means 1234.56.',
    "- Amounts: copy them exactly as printed, with their currency and separators.")


@dataclass
class Attempt:
    invoice: Invoice | None
    issues: list[Issue]
    error: str | None = None


@dataclass
class ExtractionResult:
    invoice: Invoice | None
    issues: list[Issue]
    mode_used: str
    attempts: list[Attempt] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    error: str | None = None

    @property
    def first(self) -> Attempt | None:
        """The first attempt that produced a parseable invoice (before any repair)."""
        return next((a for a in self.attempts if a.invoice is not None), None)


def _feedback(text: str) -> str:
    return f"\n\nYour previous answer had these problems:\n{text}\nRe-read the document carefully and correct them."


def extract_invoice(pdf: bytes, llm, mode: Mode = "auto", repair: bool = True, max_attempts: int = 3,
                    scans: Scans = "image", amounts: Amounts = "number") -> ExtractionResult:
    t0 = time.perf_counter()
    content = read_text(pdf)
    use_ocr = mode == "auto" and not content.has_text_layer and scans == "ocr"
    use_text = mode in ("text", "both") or (mode == "auto" and content.has_text_layer)
    use_image = mode in ("image", "both") or (mode == "auto" and not content.has_text_layer and not use_ocr)
    mode_used = "+".join(m for m, on in (("text", use_text), ("ocr", use_ocr), ("image", use_image)) if on)

    prompt = "Extract this invoice."
    if use_text:
        prompt += f"\n\nText layer of the PDF:\n{content.text}"
    if use_ocr:
        prompt += f"\n\nOCR text of the scanned PDF (may contain recognition errors):\n{ocr_text(pdf)}"
    images = page_images(pdf) if use_image else []
    system, schema, model = ((TEXT_AMOUNTS_PROMPT, TEXT_SCHEMA, InvoiceText) if amounts == "text"
                             else (SYSTEM_PROMPT, SCHEMA, Invoice))

    result = ExtractionResult(invoice=None, issues=[], mode_used=mode_used)
    feedback = ""
    try:
        for _ in range(max_attempts):
            resp = llm.extract(system, prompt + feedback, images, schema, "invoice")
            result.input_tokens += resp.input_tokens
            result.output_tokens += resp.output_tokens

            if resp.data is None:
                result.attempts.append(Attempt(None, [], "Reply was not a JSON object."))
                feedback = _feedback("- The reply was not a single JSON object matching the schema.")
                continue
            try:
                parsed = model.model_validate(resp.data)
                invoice = parsed.to_invoice() if isinstance(parsed, InvoiceText) else parsed
            except ValueError as e:  # pydantic's ValidationError is a ValueError; so is an unparsable amount
                if not isinstance(e, ValidationError):
                    result.attempts.append(Attempt(None, [], str(e)))
                    feedback = _feedback(f"- {e}")
                    continue
                errors = "\n".join(f"- {'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
                result.attempts.append(Attempt(None, [], f"Schema errors:\n{errors}"))
                feedback = _feedback(errors)
                continue

            issues = validate(invoice)
            result.attempts.append(Attempt(invoice, issues))
            if result.invoice is None or len(issues) < len(result.issues):
                result.invoice, result.issues = invoice, issues
            if not issues or not repair:
                break
            feedback = _feedback("\n".join(f"- {i.message}" for i in issues))
    except Exception as e:  # network, quota, auth: record it rather than crash a whole eval
        result.error = f"{type(e).__name__}: {e}"

    if result.invoice is None and result.error is None:
        result.error = result.attempts[-1].error if result.attempts else "no attempts"
    result.seconds = time.perf_counter() - t0
    return result
