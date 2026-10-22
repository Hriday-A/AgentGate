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
