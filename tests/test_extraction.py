"""Extraction pipeline tests with a scripted fake model, so they run offline and for free."""
import pytest

from agentgate.extraction import evaluate
from agentgate.extraction.extract import extract_invoice
from agentgate.extraction.reader import read_text
from agentgate.extraction.schema import Invoice, cents, llm_schema
from agentgate.extraction.validate import validate
from agentgate.llm import JSONResponse
from agentgate.portals import data
from agentgate.portals.friction import resolve
from agentgate.portals.pdfs import render_invoice_pdf


def as_extraction(inv: data.Invoice) -> dict:
    """What a perfect model would return for this invoice."""
    v = data.VENDORS[inv.vendor]
    return {
        "vendor_name": v["name"], "vendor_id": v["vendor_id"], "invoice_number": inv.number,
        "invoice_date": inv.issue_date.isoformat(), "due_date": inv.due_date.isoformat(),
        "po_number": inv.po_number,
        "line_items": [{"description": l["description"], "quantity": l["qty"], "unit_price": l["unit_cents"] / 100,
                        "amount": l["qty"] * l["unit_cents"] / 100} for l in inv.lines],
        "subtotal": inv.subtotal_cents / 100, "tax": inv.tax_cents / 100,
        "shipping": inv.shipping_cents / 100, "total": inv.amount_cents / 100,
    }


class ScriptedLLM:
    """Returns the queued replies in order and records what it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def extract(self, system, prompt, images, schema, name):
        self.calls.append({"prompt": prompt, "images": len(images)})
        reply = self.replies.pop(0)
        return JSONResponse(reply, str(reply), input_tokens=100, output_tokens=50)


@pytest.fixture(scope="module")
def invoices():
    env = data.generate_env("t", 11, resolve("easy"))
    return [env.invoices[v][0] for v in data.VENDORS]


# --- schema ------------------------------------------------------------------

def test_llm_schema_is_flat_and_complete():
    schema = llm_schema()
    text = str(schema)
    assert "$ref" not in text and "$defs" not in text and "title" not in schema
    assert set(schema["required"]) == set(Invoice.model_fields)
    assert schema["properties"]["line_items"]["items"]["properties"]["amount"]["type"] == "number"


def test_cents_avoids_float_artifacts():
    assert cents(0.1 + 0.2) == 30
    assert cents(1097.5) == 109750
    assert cents(19.999) == 2000


# --- validation --------------------------------------------------------------

def test_truth_passes_validation(invoices):
    for inv in invoices:
        assert validate(Invoice.model_validate(as_extraction(inv))) == []


@pytest.mark.parametrize("change, code", [
    ({"total": 1.0}, "total_math"),
    ({"subtotal": 1.0}, "lines_sum"),
    ({"due_date": "2020-01-01"}, "due_before_issue"),
    ({"invoice_date": "2030-01-01", "due_date": "2030-02-01"}, "future_date"),
    ({"vendor_id": "V-99999"}, "unknown_vendor_id"),
    ({"vendor_name": "Globex Industrial GmbH"}, "vendor_mismatch"),
    ({"line_items": []}, "no_line_items"),
])
def test_validation_catches_errors(invoices, change, code):
    inv = Invoice.model_validate({**as_extraction(invoices[0]), **change})
    assert code in {i.code for i in validate(inv)}


def test_tax_rate_check(invoices):
    acme = as_extraction(invoices[0])
    acme["tax"] += 5
    acme["total"] += 5  # keep total_math happy so only the tax-rate rule can catch it
    assert {i.code for i in validate(Invoice.model_validate(acme))} == {"tax_rate"}


# --- reader ------------------------------------------------------------------

def test_reader_detects_scans(invoices):
    inv = invoices[1]
    assert read_text(render_invoice_pdf(inv, 11)).has_text_layer
    inv.scanned = True
    try:
        assert not read_text(render_invoice_pdf(inv, 11)).has_text_layer
    finally:
        inv.scanned = False


# --- extraction loop ---------------------------------------------------------

def test_auto_mode_uses_text_for_clean_and_image_for_scanned(invoices):
    inv = invoices[0]
    llm = ScriptedLLM(as_extraction(inv), as_extraction(inv))
    clean = extract_invoice(render_invoice_pdf(inv, 11), llm)
    assert clean.mode_used == "text" and llm.calls[0]["images"] == 0 and inv.number in llm.calls[0]["prompt"]
    inv.scanned = True
    try:
        scan = extract_invoice(render_invoice_pdf(inv, 11), llm)
    finally:
        inv.scanned = False
    assert scan.mode_used == "image" and llm.calls[1]["images"] == 1


def test_schema_errors_are_retried_with_feedback(invoices):
    good = as_extraction(invoices[0])
    llm = ScriptedLLM(None, {**good, "invoice_date": "05/23/2026"}, good)
    result = extract_invoice(render_invoice_pdf(invoices[0], 11), llm)
    assert result.invoice is not None and result.issues == []
    assert len(result.attempts) == 3
    assert "invoice_date" in llm.calls[2]["prompt"]  # the Pydantic error was fed back
    assert result.input_tokens == 300


def test_validation_guided_repair(invoices):
    good = as_extraction(invoices[0])
    wrong = {**good, "total": good["total"] + 10}
    llm = ScriptedLLM(wrong, good)
    result = extract_invoice(render_invoice_pdf(invoices[0], 11), llm)
    assert result.issues == [] and len(result.attempts) == 2
    assert "does not equal the total" in llm.calls[1]["prompt"]
    assert result.first.issues  # the first attempt was the wrong one


def test_repair_can_be_disabled(invoices):
    good = as_extraction(invoices[0])
    llm = ScriptedLLM({**good, "total": good["total"] + 10})
    result = extract_invoice(render_invoice_pdf(invoices[0], 11), llm, repair=False)
    assert len(result.attempts) == 1 and result.issues


def test_best_attempt_wins_when_repair_makes_it_worse(invoices):
    good = as_extraction(invoices[0])
    one_issue = {**good, "total": good["total"] + 10}
    two_issues = {**one_issue, "vendor_id": "V-0"}
    llm = ScriptedLLM(one_issue, two_issues, two_issues)
    result = extract_invoice(render_invoice_pdf(invoices[0], 11), llm)
    assert [i.code for i in result.issues] == ["total_math"]


def test_provider_errors_are_recorded_not_raised(invoices):
    class Broken:
        def extract(self, *a):
            raise RuntimeError("quota exceeded")
    result = extract_invoice(render_invoice_pdf(invoices[0], 11), Broken())
    assert result.invoice is None and "quota exceeded" in result.error


# --- eval harness ------------------------------------------------------------

def test_samples_are_deterministic_and_balanced():
    a, b = evaluate.make_samples(12, 0.5), evaluate.make_samples(12, 0.5)
    assert [s.id for s in a] == [s.id for s in b]
    assert sum(s.invoice.scanned for s in a) == 6
    assert {s.template for s in a} == {"classic", "european", "statement"}


def _oracle_extractor(samples, corrupt=None):
    by_pdf = {s.pdf(): s.invoice for s in samples}

    def extractor(pdf):
        reply = as_extraction(by_pdf[pdf])
        if corrupt and by_pdf[pdf].vendor == corrupt:
            reply["po_number"] = "PO-000000"  # an error validation cannot see
        return extract_invoice(pdf, ScriptedLLM(reply))
    return extractor


def test_eval_perfect_extractor_scores_100():
    samples = evaluate.make_samples(6, 0.5)
    results = evaluate.run(samples, _oracle_extractor(samples), {"model": "fake"})
    s = results["summary"]
    assert s["doc_accuracy"] == 1.0 and s["field_accuracy"] == 1.0
    assert s["validator_recall"] is None  # nothing was wrong


def test_eval_scores_field_errors_and_validator_blind_spots():
    samples = evaluate.make_samples(6, 0.0)
    results = evaluate.run(samples, _oracle_extractor(samples, corrupt="acme"), {"model": "fake"})
    s = results["summary"]
    assert s["per_field"]["po_number"] == pytest.approx(4 / 6, abs=1e-3)
    assert s["doc_accuracy"] == pytest.approx(4 / 6, abs=1e-3)
    assert s["validator_recall"] == 0.0  # wrong PO numbers pass every business rule


# --- amounts as printed text, parsed in code ---------------------------------

from agentgate.extraction.schema import InvoiceText, parse_amount  # noqa: E402


@pytest.mark.parametrize("text, value", [
    ("$1,234.50", 1234.5), ("1.234,50 USD", 1234.5), ("USD 1234.50", 1234.5), ("13.415,68 USD", 13415.68),
    ("0,00 USD", 0.0), ("$0.00", 0.0), ("USD 15.00", 15.0), ("1,234", 1234.0), ("1.234", 1234.0),
    ("12.345.678,90", 12345678.9), ("-$5.10", -5.1), ("49,99", 49.99),
])
def test_parse_amount_us_and_european(text, value):
    assert parse_amount(text) == pytest.approx(value)


def test_parse_amount_rejects_non_numbers():
    with pytest.raises(ValueError):
        parse_amount("n/a")


def printed(inv: data.Invoice) -> dict:
    """What a model copying amounts verbatim would return (vendor-formatted strings)."""
    m = lambda c: data.fmt_money(c, inv.vendor)  # noqa: E731
    reply = as_extraction(inv)
    reply.update(subtotal=m(inv.subtotal_cents), tax=m(inv.tax_cents), shipping=m(inv.shipping_cents),
                 total=m(inv.amount_cents))
    reply["line_items"] = [{"description": l["description"], "quantity": l["qty"], "unit_price": m(l["unit_cents"]),
                            "amount": m(l["qty"] * l["unit_cents"])} for l in inv.lines]
    return reply


def test_text_amounts_round_trip_for_every_vendor_format(invoices):
    for inv in invoices:  # US, European and "USD 1234.50" styles
        result = extract_invoice(render_invoice_pdf(inv, 11), ScriptedLLM(printed(inv)), amounts="text")
        assert result.issues == [], (inv.vendor, result.issues)
        assert evaluate.score(result.invoice, evaluate.truth(inv)) == {f: True for f in evaluate.FIELDS}


def test_unparsable_text_amount_is_fed_back(invoices):
    bad = {**printed(invoices[0]), "total": "see attached"}
    llm = ScriptedLLM(bad, printed(invoices[0]))
    result = extract_invoice(render_invoice_pdf(invoices[0], 11), llm, amounts="text")
    assert len(result.attempts) == 2 and "see attached" in llm.calls[1]["prompt"]


def test_text_schema_has_string_amounts():
    schema = llm_schema(InvoiceText)
    assert schema["properties"]["total"]["type"] == "string"
    assert schema["properties"]["line_items"]["items"]["properties"]["amount"]["type"] == "string"


# --- OCR for scans -----------------------------------------------------------

import shutil  # noqa: E402

from agentgate.extraction.reader import ocr_text  # noqa: E402


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract not installed")
def test_ocr_reads_a_scanned_invoice(invoices):
    inv = invoices[0]
    inv.scanned = True
    try:
        pdf = render_invoice_pdf(inv, 11)
    finally:
        inv.scanned = False
    text = ocr_text(pdf)
    assert inv.number in text and inv.po_number in text


def test_ocr_scan_strategy_sends_text_not_images(invoices, monkeypatch):
    import agentgate.extraction.extract as ex
    monkeypatch.setattr(ex, "ocr_text", lambda pdf: "OCR SAYS HELLO")
    inv = invoices[1]
    inv.scanned = True
    try:
        llm = ScriptedLLM(as_extraction(inv))
        result = extract_invoice(render_invoice_pdf(inv, 11), llm, scans="ocr")
    finally:
        inv.scanned = False
    assert result.mode_used == "ocr" and llm.calls[0]["images"] == 0 and "OCR SAYS HELLO" in llm.calls[0]["prompt"]
