"""Deterministic invoice data and per-vendor presentation for the mock portals.

Two independent sources of randomness:
  * invoice data comes only from the seed, so the same seed gives the same
    invoices at every difficulty level;
  * layout quirks come from the seed and the friction settings.
That separation lets an eval compare difficulty levels on identical data.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from .friction import Friction, resolve

# Fixture credentials for the mock portals only.
CREDENTIALS = {"username": "ap-clerk@agentgate.test", "password": "demo-password"}

# Fixed "today" so due/overdue logic is reproducible across runs.
TODAY = date(2026, 9, 30)

# The fictional company that receives the invoices.
BILL_TO = ["Northwind Analytics LLC", "Attn: Accounts Payable", "500 Prairie Ave, Suite 210", "Champaign, IL 61820"]

VENDORS = {
    "acme": {
        "name": "Acme Supply Co.",
        "vendor_id": "V-20481",
        "customer_no": "CUST-0042817",
        "address": ["1200 Industrial Pkwy", "Dayton, OH 45402", "billing@acme-supply.test"],
        "date_fmt": "%m/%d/%Y",
        "money": "us",
        "tax_bp": 825,  # basis points: 825 = 8.25%
        "tax_label": "Sales tax (8.25%)",
        "terms": [15, 30],
        "pdf_template": "classic",
        "color": "#b03a2e",
        "heading": "Your Invoices",
    },
    "globex": {
        "name": "Globex Industrial GmbH",
        "vendor_id": "V-31977",
        "customer_no": "KD-778120",
        "address": ["Hafenstrasse 18", "20457 Hamburg, Germany", "rechnung@globex.test"],
        "date_fmt": "%d.%m.%Y",
        "money": "eu",
        "tax_bp": 1900,
        "tax_label": "MwSt. 19% / VAT",
        "terms": [30, 45, 60],
        "pdf_template": "european",
        "color": "#283747",
        "heading": "Rechnungen / Invoices",
    },
    "initech": {
        "name": "Initech Office Solutions",
        "vendor_id": "V-48802",
        "customer_no": "ACCT 5531-09",
        "address": ["4120 Freidrich Ln, Ste 300", "Austin, TX 78744", "ar@initech.test"],
        "date_fmt": "%b %d, %Y",
        "money": "code",
        "tax_bp": 0,
        "tax_label": "TAX",
        "terms": [30, 45],
        "pdf_template": "statement",
        "color": "#117a65",
        "heading": "Billing Documents",
    },
}

PRODUCTS = [
    "Copy paper, A4 (case)", "Toner cartridge TN-760", "Ergonomic chair", "Standing desk frame",
    "Pallet wrap 18in", "Nitrile gloves (box)", "Hydraulic fitting 3/8", "Safety goggles",
    "Label printer rolls", "Server rack shelf", "Cat6 patch cable 3m", "Whiteboard markers",
]

# --- presentation vocabulary -------------------------------------------------

COLUMNS = ["number", "issue_date", "due_date", "amount", "status"]
CANONICAL_LABELS = {"number": "Invoice #", "issue_date": "Invoice Date", "due_date": "Due Date",
                    "amount": "Total", "status": "Status"}
LABEL_VARIANTS = {
    "number": ["Invoice No.", "Document", "Ref", "Beleg-Nr."],
    "issue_date": ["Date", "Issued", "Billing Date", "Doc. date"],
    "due_date": ["Due", "Payment Due", "Pay By", "Net date"],
    "amount": ["Amount", "Balance", "Amount Due", "Gross"],
    "status": ["State", "Payment Status", "Settlement"],
}
CANONICAL_STATUS = {"paid": "Paid", "unpaid": "Unpaid", "overdue": "Overdue"}
STATUS_VARIANTS = {"paid": ["Settled", "Closed", "Cleared"], "unpaid": ["Open", "Outstanding", "Pending"],
                   "overdue": ["Past due", "Overdue", "Late"]}
CANONICAL_BUTTONS = {"sign_in": "Sign in", "view": "View", "download": "Download PDF",
                     "next": "Next ›", "prev": "‹ Previous", "sign_out": "Sign out"}
BUTTON_VARIANTS = {
    "sign_in": ["Log in", "Continue", "Access account", "Submit"],
    "view": ["Open", "Details", "Show", "›"],
    "download": ["Get PDF", "Original document", "Export", "Download"],
    "next": ["More", "Older ›", "Next page", "»"],
    "prev": ["Newer", "‹ Back", "Previous page", "«"],
    "sign_out": ["Log out", "Exit", "End session"],
}


@dataclass
class Invoice:
    vendor: str
    number: str
    issue_date: date
    terms_days: int
    lines: list[dict]
    subtotal_cents: int
    tax_cents: int
    shipping_cents: int
    status: str  # "paid" | "unpaid"
    po_number: str
    scanned: bool = False  # PDF is an image-only "scan" with no text layer

    @property
    def due_date(self) -> date:
        return self.issue_date + timedelta(days=self.terms_days)

    @property
    def amount_cents(self) -> int:
        return self.subtotal_cents + self.tax_cents + self.shipping_cents

    @property
    def overdue(self) -> bool:
        return self.status == "unpaid" and self.due_date < TODAY

    def to_json(self) -> dict:
        return {
            "vendor": self.vendor,
            "vendor_id": VENDORS[self.vendor]["vendor_id"],
            "number": self.number,
            "issue_date": self.issue_date.isoformat(),
            "due_date": self.due_date.isoformat(),
            "terms_days": self.terms_days,
            "po_number": self.po_number,
            "lines": self.lines,
            "subtotal_cents": self.subtotal_cents,
            "tax_cents": self.tax_cents,
            "shipping_cents": self.shipping_cents,
            "amount_cents": self.amount_cents,
            "status": self.status,
            "overdue": self.overdue,
            "scanned": self.scanned,
        }


@dataclass
class Layout:
    """How one vendor portal presents itself in one environment."""

    columns: list[str]
    labels: dict[str, str]
    status_text: dict[str, str]
    buttons: dict[str, str]
    newest_first: bool
    page_size: int | None              # None = everything on one page
    cookie_banner: bool
    promo_modal: bool
    session_max_requests: int | None   # None = sessions never expire


@dataclass
class Env:
    id: str
    seed: int
    friction: Friction
    invoices: dict[str, list[Invoice]]
    layouts: dict[str, Layout]
    ap_entries: list[dict] = field(default_factory=list)
    pdf_cache: dict[str, bytes] = field(default_factory=dict)

    def find_invoice(self, vendor: str, number: str) -> Invoice | None:
        return next((i for i in self.invoices[vendor] if i.number == number), None)

    def to_json(self) -> dict:
        return {
            "env_id": self.id,
            "seed": self.seed,
            "today": TODAY.isoformat(),
            "friction": self.friction.model_dump(),
            "vendors": {k: {"name": v["name"], "vendor_id": v["vendor_id"]} for k, v in VENDORS.items()},
            "invoices": {k: [i.to_json() for i in v] for k, v in self.invoices.items()},
            "ap_entries": list(self.ap_entries),
        }


# --- formatting --------------------------------------------------------------

def fmt_date(d: date, vendor: str) -> str:
    return d.strftime(VENDORS[vendor]["date_fmt"])


def fmt_money(cents: int, vendor: str) -> str:
    whole, frac = divmod(cents, 100)
    style = VENDORS[vendor]["money"]
    if style == "us":
        return f"${whole:,}.{frac:02d}"
    if style == "eu":
        return f"{whole:,}".replace(",", ".") + f",{frac:02d} USD"
    return f"USD {whole}.{frac:02d}"


# --- generation --------------------------------------------------------------

def _invoice_number(rng: random.Random, vendor: str, used: set[str]) -> str:
    while True:
        n = rng.randint(1000, 99999)
        number = {
            "acme": f"ACM-{n:05d}",
            "globex": f"2026-GLX-{n % 10000:04d}",
            "initech": f"INI{n:05d}-{rng.choice('ABC')}",
        }[vendor]
        if number not in used:
            used.add(number)
            return number


def _make_invoices(rng: random.Random, vendor: str) -> list[Invoice]:
    cfg = VENDORS[vendor]
    used_numbers: set[str] = set()
    used_ages: set[int] = set()
    invoices = []
    for _ in range(rng.randint(7, 14)):
        age = rng.randint(3, 180)
        while age in used_ages:  # unique issue dates keep "oldest" unambiguous
            age = rng.randint(3, 180)
        used_ages.add(age)
        lines = [
            {"description": rng.choice(PRODUCTS), "qty": rng.randint(1, 40), "unit_cents": rng.randint(250, 45000)}
            for _ in range(rng.randint(1, 6))
        ]
        subtotal = sum(line["qty"] * line["unit_cents"] for line in lines)
        invoices.append(
            Invoice(
                vendor=vendor,
                number=_invoice_number(rng, vendor, used_numbers),
                issue_date=TODAY - timedelta(days=age),
                terms_days=rng.choice(cfg["terms"]),
                lines=lines,
                subtotal_cents=subtotal,
                # Integer rounding to the nearest cent: (x * bp + 5000) // 10000.
                tax_cents=(subtotal * cfg["tax_bp"] + 5000) // 10000,
                shipping_cents=rng.choice([0, 0, 1500, 2500, 4999]),
                status="paid" if rng.random() < (0.75 if age > 60 else 0.3) else "unpaid",
                po_number=f"PO-{rng.randint(100000, 999999)}",
            )
        )
    unpaid = [i for i in invoices if i.status == "unpaid"]
    for inv in invoices:  # every vendor needs at least two open invoices
        if len(unpaid) >= 2:
            break
        if inv.status == "paid":
            inv.status = "unpaid"
            unpaid.append(inv)
    return invoices


def _make_layout(seed: int, vendor: str, friction: Friction) -> Layout:
    # String seeds are hashed deterministically by random.Random (not affected by PYTHONHASHSEED).
    rng = random.Random(f"{seed}:layout:{vendor}")
    columns, labels = list(COLUMNS), dict(CANONICAL_LABELS)
    status_text, newest_first = dict(CANONICAL_STATUS), True
    if friction.shuffle_layout:
        rest = columns[1:]
        rng.shuffle(rest)
        columns = [columns[0], *rest]
        labels = {k: rng.choice(v) for k, v in LABEL_VARIANTS.items()}
        status_text = {k: rng.choice(v) for k, v in STATUS_VARIANTS.items()}
        newest_first = rng.random() < 0.5
    buttons = ({k: rng.choice(v) for k, v in BUTTON_VARIANTS.items()}
               if friction.renamed_buttons else dict(CANONICAL_BUTTONS))
    return Layout(
        columns=columns,
        labels=labels,
        status_text=status_text,
        buttons=buttons,
        newest_first=newest_first,
        page_size=rng.choice([4, 5, 6]) if friction.pagination else None,
        cookie_banner=friction.popups,
        promo_modal=friction.popups,
        session_max_requests=friction.session_max_requests if friction.session_timeout else None,
    )


def generate_env(env_id: str, seed: int, friction: Friction | None = None) -> Env:
    friction = friction or resolve("medium")
    rng = random.Random(seed)
    invoices = {v: _make_invoices(rng, v) for v in VENDORS}
    for vendor_invoices in invoices.values():
        for inv in vendor_invoices:
            # One draw per invoice, compared to the rate: raising the rate only adds scans,
            # so every invoice scanned at "medium" is also scanned at "hard".
            inv.scanned = random.Random(f"{seed}:scan:{inv.number}").random() < friction.scanned_pdf_rate
    layouts = {v: _make_layout(seed, v, friction) for v in VENDORS}
    return Env(id=env_id, seed=seed, friction=friction, invoices=invoices, layouts=layouts)
