"""The internal accounts-payable app where the agent records invoices.

It validates like a real AP system: strict formats, totals that must add up,
and no duplicate invoices per vendor.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from . import data
from .state import LOCK, get_env, templates

router = APIRouter(prefix="/e/{env_id}/ap")

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PLAIN_AMOUNT = re.compile(r"^\d+(\.\d{1,2})?$")
MONEY_FIELDS = [("subtotal", "Subtotal"), ("tax", "Tax"), ("shipping", "Shipping"), ("total", "Total")]


def _cents(value: str) -> int:
    return int(Decimal(value) * 100)


def validate_entry(form: dict[str, str], existing: list[dict]) -> tuple[dict | None, list[str]]:
    """Return (entry, []) if the form is valid, else (None, errors). Pure, so it's easy to unit test."""
    f = {k: (v or "").strip() for k, v in form.items()}
    errors: list[str] = []

    if f.get("vendor") not in data.VENDORS:
        errors.append("Choose a vendor.")
    if not f.get("invoice_number"):
        errors.append("Invoice number is required.")
    if not f.get("po_number"):
        errors.append("PO number is required.")

    dates: dict[str, date] = {}
    for key, label in (("invoice_date", "Invoice date"), ("due_date", "Due date")):
        value = f.get(key, "")
        if not ISO_DATE.match(value):
            errors.append(f"{label} must be in YYYY-MM-DD format.")
            continue
        try:
            dates[key] = date.fromisoformat(value)
        except ValueError:
            errors.append(f"{label} is not a valid date.")
    if len(dates) == 2 and dates["due_date"] < dates["invoice_date"]:
        errors.append("Due date cannot be before the invoice date.")

    amounts: dict[str, int] = {}
    for key, label in MONEY_FIELDS:
        value = f.get(key, "")
        if PLAIN_AMOUNT.match(value):
            amounts[key] = _cents(value)
        else:
            errors.append(f"{label} must be a plain number like 1234.56 (no currency symbols or separators).")
    if len(amounts) == 4 and amounts["subtotal"] + amounts["tax"] + amounts["shipping"] != amounts["total"]:
        errors.append("Subtotal + tax + shipping must equal the total.")

    number = f.get("invoice_number", "")
    if any(e["vendor"] == f.get("vendor") and e["invoice_number"].lower() == number.lower() for e in existing):
        errors.append(f"Invoice {number} from this vendor has already been recorded.")

    if errors:
        return None, errors
    return {
        "vendor": f["vendor"],
        "invoice_number": number,
        "invoice_date": f["invoice_date"],
        "due_date": f["due_date"],
        "po_number": f["po_number"],
        "subtotal_cents": amounts["subtotal"],
        "tax_cents": amounts["tax"],
        "shipping_cents": amounts["shipping"],
        "amount_cents": amounts["total"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, []


def _money(cents: int) -> str:
    return f"{cents // 100:,}.{cents % 100:02d}"


@router.get("/", name="ap_list")
def ap_list(request: Request, env: data.Env = Depends(get_env)):
    return templates.TemplateResponse(request, "ap_list.html", {
        "env": env, "vendors": data.VENDORS, "saved": request.query_params.get("saved"), "money": _money,
        "color": "#4a3f8f"})


@router.get("/new", name="ap_new")
def ap_new_form(request: Request, env: data.Env = Depends(get_env)):
    return templates.TemplateResponse(request, "ap_new.html", {
        "env": env, "vendors": data.VENDORS, "errors": [], "form": {}, "color": "#4a3f8f"})


@router.post("/new")
async def ap_new_submit(request: Request, env: data.Env = Depends(get_env)):
    form = {k: str(v) for k, v in (await request.form()).items()}
    with LOCK:  # validate and append atomically so concurrent submits can't both pass the duplicate check
        entry, errors = validate_entry(form, env.ap_entries)
        if entry:
            env.ap_entries.append(entry)
    if entry:
        url = request.url_for("ap_list", env_id=env.id).include_query_params(saved=entry["invoice_number"])
        return RedirectResponse(str(url), status_code=303)
    return templates.TemplateResponse(request, "ap_new.html", {
        "env": env, "vendors": data.VENDORS, "errors": errors, "form": form, "color": "#4a3f8f"},
        status_code=422)
