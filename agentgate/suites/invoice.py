"""Invoice-processing suite: task kinds, their prompts and their checkers.

A task in invoice.yaml names a kind, a vendor, a difficulty and a seed. The
kind turns the environment's initial state into a prompt and a checker, so
expected values always come from the same generator the portals use.
"""
from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

import yaml

from ..portals.data import CREDENTIALS

SUITE_FILE = Path(__file__).with_name("invoice.yaml")

CREDS = f"Vendor portal login: username {CREDENTIALS['username']}, password {CREDENTIALS['password']}."
ANSWER_NUMBER = "Answer with just the number in US dollars, e.g. 1234.56."


@dataclass
class Task:
    id: str
    kind: str
    vendor: str
    seed: int
    difficulty: str = "medium"
    smoke: bool = False  # part of the small subset CI runs on every pull request


@dataclass
class Prepared:
    prompt: str
    expected: object
    check: Callable[[dict, str | None], tuple[bool, str]]
    # Data-entry tasks also score each AP field, so partial credit is visible in the metrics.
    fields: Callable[[dict], dict[str, bool]] | None = None


def load_tasks(path: Path = SUITE_FILE) -> list[Task]:
    raw = yaml.safe_load(path.read_text())
    return [Task(**t) for t in raw["tasks"]]


# --- answer parsing ----------------------------------------------------------

def parse_money(answer: str | None) -> Decimal | None:
    if not answer:
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", answer.replace("$", ""))
    if not m:
        return None
    try:
        return Decimal(m.group().replace(",", ""))
    except InvalidOperation:
        return None


def parse_iso_date(answer: str | None) -> date | None:
    m = re.search(r"\d{4}-\d{2}-\d{2}", answer or "")
    try:
        return date.fromisoformat(m.group()) if m else None
    except ValueError:
        return None


def _cents_str(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def check_money(expected_cents: int):
    def check(_state: dict, answer: str | None) -> tuple[bool, str]:
        got = parse_money(answer)
        if got is None:
            return False, f"could not parse an amount from {answer!r}"
        ok = abs(got * 100 - expected_cents) < 1
        return ok, f"expected {_cents_str(expected_cents)}, got {got}"
    return check


def check_int(expected: int):
    def check(_state: dict, answer: str | None) -> tuple[bool, str]:
        got = parse_money(answer)
        if got is None:
            return False, f"could not parse a number from {answer!r}"
        return got == expected, f"expected {expected}, got {got}"
    return check


def check_date(expected: str):
    def check(_state: dict, answer: str | None) -> tuple[bool, str]:
        got = parse_iso_date(answer)
        if got is None:
            return False, f"no YYYY-MM-DD date in {answer!r}"
        return got.isoformat() == expected, f"expected {expected}, got {got.isoformat()}"
    return check


AP_FIELDS = {  # AP entry key -> ground-truth invoice key
    "invoice_number": "number", "invoice_date": "issue_date", "due_date": "due_date", "po_number": "po_number",
    "subtotal_cents": "subtotal_cents", "tax_cents": "tax_cents", "shipping_cents": "shipping_cents",
    "amount_cents": "amount_cents",
}


def ap_field_scores(vendor: str, inv: dict):
    """Per-field correctness of the single AP entry (all False if there isn't exactly one)."""
    def scores(state: dict) -> dict[str, bool]:
        entries = state["ap_entries"]
        if len(entries) != 1:
            return {"vendor": False, **{k: False for k in AP_FIELDS}}
        e = entries[0]
        result = {"vendor": e["vendor"] == vendor}
        for key, truth_key in AP_FIELDS.items():
            got, want = e.get(key), inv[truth_key]
            result[key] = (got.strip().lower() == want.lower()) if key == "invoice_number" else got == want
        return result
    return scores


def check_ap_entry(vendor: str, inv: dict):
    scores = ap_field_scores(vendor, inv)

    def check(state: dict, _answer: str | None) -> tuple[bool, str]:
        entries = state["ap_entries"]
        if len(entries) != 1:
            return False, f"expected exactly 1 AP entry, found {len(entries)}"
        wrong = [k for k, ok in scores(state).items() if not ok]
        e = entries[0]
        details = [f"{k} {e.get(k)!r} != {inv[AP_FIELDS[k]]!r}" if k in AP_FIELDS else f"vendor {e['vendor']} != {vendor}"
                   for k in wrong]
        return not wrong, "; ".join(details) or "entry matches"
    return check


# --- task kinds --------------------------------------------------------------

def _pick(task: Task, invoices: list[dict]) -> dict:
    return invoices[zlib.crc32(task.id.encode()) % len(invoices)]


def _unpaid(invoices: list[dict]) -> list[dict]:
    return [i for i in invoices if i["status"] != "paid"]


def prepare(task: Task, state: dict) -> Prepared:
    vname = state["vendors"][task.vendor]["name"]
    invoices = state["invoices"][task.vendor]
    open_note = "Count overdue invoices as unpaid."

    if task.kind == "lookup_amount":
        inv = _pick(task, invoices)
        prompt = (f"Sign in to the {vname} vendor portal and find invoice {inv['number']}. "
                  f"What is its total amount, including tax and shipping? {ANSWER_NUMBER}")
        return Prepared(f"{prompt} {CREDS}", inv["amount_cents"], check_money(inv["amount_cents"]))

    if task.kind == "lookup_due_date":
        inv = _pick(task, invoices)
        prompt = (f"Sign in to the {vname} vendor portal and find invoice {inv['number']}. "
                  "When is payment due? Answer with the date in YYYY-MM-DD format.")
        return Prepared(f"{prompt} {CREDS}", inv["due_date"], check_date(inv["due_date"]))

    if task.kind == "count_unpaid":
        n = len(_unpaid(invoices))
        prompt = (f"How many invoices from {vname} are not yet paid? {open_note} "
                  "Check their vendor portal and answer with just the number.")
        return Prepared(f"{prompt} {CREDS}", n, check_int(n))

    if task.kind == "total_outstanding":
        total = sum(i["amount_cents"] for i in _unpaid(invoices))
        prompt = (f"What is the total amount we still owe {vname} across all unpaid invoices? {open_note} "
                  f"Check their vendor portal. {ANSWER_NUMBER}")
        return Prepared(f"{prompt} {CREDS}", total, check_money(total))

    if task.kind == "enter_invoice":
        inv = _pick(task, invoices)
        prompt = (f"Record invoice {inv['number']} from {vname} in our Accounts Payable app. "
                  "Download the invoice PDF from their vendor portal and copy every field the AP form asks for.")
        return Prepared(f"{prompt} {CREDS}", inv, check_ap_entry(task.vendor, inv), ap_field_scores(task.vendor, inv))

    if task.kind == "enter_oldest_unpaid":
        inv = min(_unpaid(invoices), key=lambda i: i["issue_date"])
        prompt = (f"Find the oldest unpaid invoice (by invoice date) in the {vname} vendor portal and record it "
                  f"in our Accounts Payable app, using the invoice PDF for every field the form asks for. {open_note}")
        return Prepared(f"{prompt} {CREDS}", inv, check_ap_entry(task.vendor, inv), ap_field_scores(task.vendor, inv))

    raise ValueError(f"Unknown task kind {task.kind!r}")
