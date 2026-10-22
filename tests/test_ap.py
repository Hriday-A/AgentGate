import pytest
from fastapi.testclient import TestClient

from agentgate.portals.ap import validate_entry
from agentgate.portals.app import app

VALID = {
    "vendor": "acme", "invoice_number": "ACM-1", "invoice_date": "2026-09-01", "due_date": "2026-10-01",
    "po_number": "PO-123456", "subtotal": "1000.00", "tax": "82.50", "shipping": "15.00", "total": "1097.50",
}


def test_valid_entry_is_converted_to_cents():
    entry, errors = validate_entry(VALID, [])
    assert errors == []
    assert (entry["subtotal_cents"], entry["tax_cents"], entry["shipping_cents"], entry["amount_cents"]) == (
        100000, 8250, 1500, 109750)


@pytest.mark.parametrize("field, value, message", [
    ("total", "$1,097.50", "plain number"),
    ("total", "1097.49", "must equal the total"),
    ("invoice_date", "09/01/2026", "YYYY-MM-DD"),
    ("due_date", "2026-02-30", "not a valid date"),
    ("due_date", "2026-08-01", "before the invoice date"),
    ("vendor", "umbrella", "Choose a vendor"),
    ("po_number", "", "PO number is required"),
])
def test_validation_errors(field, value, message):
    entry, errors = validate_entry({**VALID, field: value}, [])
    assert entry is None
    assert any(message in e for e in errors), errors


def test_duplicates_are_rejected_case_insensitively():
    first, _ = validate_entry(VALID, [])
    _, errors = validate_entry({**VALID, "invoice_number": "acm-1"}, [first])
    assert any("already been recorded" in e for e in errors)


def test_ap_form_round_trip():
    with TestClient(app) as client:
        env_id = client.post("/admin/envs", json={"seed": 1}).json()["env_id"]
        bad = client.post(f"/e/{env_id}/ap/new", data={**VALID, "tax": "x"})
        assert bad.status_code == 422 and 'value="ACM-1"' in bad.text  # form keeps what was typed
        ok = client.post(f"/e/{env_id}/ap/new", data=VALID, follow_redirects=False)
        assert ok.status_code == 303
        entries = client.get(f"/admin/envs/{env_id}/state").json()["ap_entries"]
        assert [e["invoice_number"] for e in entries] == ["ACM-1"]
