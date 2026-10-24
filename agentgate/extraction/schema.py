"""The structured-output contract: what an extracted invoice looks like.

The Pydantic model is the single source of truth. `llm_schema()` derives the
JSON schema sent to the model from it, simplified to the subset of JSON Schema
that every provider's structured-output mode accepts.
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field

AMOUNT = "Plain number in the invoice currency, no symbols or thousands separators, e.g. 1234.56"


class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float = Field(description=AMOUNT)
    amount: float = Field(description="Line total. " + AMOUNT)


class Invoice(BaseModel):
    vendor_name: str = Field(description="Supplier's company name as printed")
    vendor_id: str = Field(description="Supplier/vendor ID printed on the document (not the customer number "
                                       "or tax ID), e.g. V-12345. Empty string if absent.")
    invoice_number: str = Field(description="Copy exactly as printed")
    invoice_date: date = Field(description="YYYY-MM-DD")
    due_date: date = Field(description="Payment due date, YYYY-MM-DD")
    po_number: str = Field(description="Customer purchase-order number, copied exactly. Empty string if absent.")
    line_items: list[LineItem]
    subtotal: float = Field(description="Sum of line items before tax and shipping. " + AMOUNT)
    tax: float = Field(description=AMOUNT)
    shipping: float = Field(description="Shipping/freight. 0 if absent. " + AMOUNT)
    total: float = Field(description="Amount payable for THIS invoice only (ignore previous balances). " + AMOUNT)


AMOUNT_TEXT = "Copy exactly as printed, including currency and separators, e.g. '13.415,68 USD' or '$1,234.50'"


class LineItemText(BaseModel):
    description: str
    quantity: float
    unit_price: str = Field(description=AMOUNT_TEXT)
    amount: str = Field(description="Line total. " + AMOUNT_TEXT)


class InvoiceText(BaseModel):
    """Same fields as Invoice, but amounts stay as printed text and are parsed by code (parse_amount).

    Small models often misread "13.415,68" as 13.41568. Copying text is easy for them; deciding which
    separator is the decimal point is exactly the kind of rule code gets right every time.
    """
    vendor_name: str = Invoice.model_fields["vendor_name"]
    vendor_id: str = Invoice.model_fields["vendor_id"]
    invoice_number: str = Invoice.model_fields["invoice_number"]
    invoice_date: date = Invoice.model_fields["invoice_date"]
    due_date: date = Invoice.model_fields["due_date"]
    po_number: str = Invoice.model_fields["po_number"]
    line_items: list[LineItemText]
    subtotal: str = Field(description="Subtotal before tax and shipping. " + AMOUNT_TEXT)
    tax: str = Field(description=AMOUNT_TEXT)
    shipping: str = Field(description="Shipping/freight; '0' if absent. " + AMOUNT_TEXT)
    total: str = Field(description="Amount payable for THIS invoice only. " + AMOUNT_TEXT)

    def to_invoice(self) -> "Invoice":
        data = self.model_dump()
        for key in ("subtotal", "tax", "shipping", "total"):
            data[key] = parse_amount(data[key])
        for item in data["line_items"]:
            item["unit_price"] = parse_amount(item["unit_price"])
            item["amount"] = parse_amount(item["amount"])
        return Invoice.model_validate(data)


def parse_amount(text: str) -> float:
    """Parse a printed amount in US or European style: "$1,234.50", "1.234,50 USD", "USD 1234.50", "0,00".

    Rule: the LAST separator is the decimal point if 1–2 digits follow it; any other separator groups
    thousands. (Invoices print cents, so "1.234" with three trailing digits means one thousand two hundred.)
    """
    cleaned = "".join(ch for ch in str(text) if ch.isdigit() or ch in ".,-")
    if not any(ch.isdigit() for ch in cleaned):
        raise ValueError(f"No number in amount {text!r}")
    negative = cleaned.startswith("-")
    cleaned = cleaned.replace("-", "")
    last = max(cleaned.rfind("."), cleaned.rfind(","))
    if last != -1 and 1 <= len(cleaned) - last - 1 <= 2:
        whole, frac = cleaned[:last], cleaned[last + 1:]
    else:
        whole, frac = cleaned, ""
    whole = whole.replace(".", "").replace(",", "") or "0"
    value = float(f"{whole}.{frac or '0'}")
    return -value if negative else value


def cents(amount: float) -> int:
    """Convert a 2-decimal amount to integer cents without float artifacts (0.1 + 0.2 problems)."""
    return int((Decimal(str(amount)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def llm_schema(model: type[BaseModel] = Invoice) -> dict:
    """JSON schema for structured output: $refs inlined, titles and formats dropped."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def simplify(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return simplify(defs[node["$ref"].split("/")[-1]])
            return {k: simplify(v) for k, v in node.items() if k not in ("title", "format")}
        if isinstance(node, list):
            return [simplify(v) for v in node]
        return node

    return simplify(schema)
