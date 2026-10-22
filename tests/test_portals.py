import re

import pytest
from fastapi.testclient import TestClient

from agentgate.portals import data
from agentgate.portals.app import app
from agentgate.portals.friction import PRESETS, resolve


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def make_env(client, seed=7, difficulty="medium", **friction):
    resp = client.post("/admin/envs", json={"seed": seed, "difficulty": difficulty, "friction": friction})
    assert resp.status_code == 200, resp.text
    return resp.json()["env_id"]


def state(client, env_id):
    return client.get(f"/admin/envs/{env_id}/state").json()


def login(client, env_id, vendor, **extra):
    return client.post(f"/e/{env_id}/{vendor}/login", data={**data.CREDENTIALS, **extra}, follow_redirects=False)


# --- data generation ---------------------------------------------------------

def test_generation_is_deterministic():
    a, b = data.generate_env("a", 42).to_json(), data.generate_env("b", 42).to_json()
    assert a["invoices"] == b["invoices"]
    assert data.generate_env("c", 43).to_json()["invoices"] != a["invoices"]


def test_same_seed_gives_same_invoices_at_every_difficulty():
    def strip(env):  # everything except the scanned flag, which depends on friction
        return {v: [{k: x for k, x in i.items() if k != "scanned"} for i in invs]
                for v, invs in env.to_json()["invoices"].items()}
    easy, hard = (data.generate_env("x", 5, resolve(d)) for d in ("easy", "hard"))
    assert strip(easy) == strip(hard)


def test_scans_are_monotonic_in_rate():
    medium, hard = (data.generate_env("x", 9, resolve(d)) for d in ("medium", "hard"))
    scanned = lambda env: {i.number for invs in env.invoices.values() for i in invs if i.scanned}  # noqa: E731
    assert scanned(medium) <= scanned(hard)
    assert not scanned(data.generate_env("x", 9, resolve("easy")))


@pytest.mark.parametrize("seed", range(40))
def test_invoice_invariants(seed):
    env = data.generate_env("x", seed)
    for vendor, invoices in env.invoices.items():
        assert sum(i.status == "unpaid" for i in invoices) >= 2
        assert len({i.issue_date for i in invoices}) == len(invoices)
        for inv in invoices:
            assert sum(l["qty"] * l["unit_cents"] for l in inv.lines) == inv.subtotal_cents
            assert inv.amount_cents == inv.subtotal_cents + inv.tax_cents + inv.shipping_cents
            assert inv.due_date > inv.issue_date


def test_money_formats():
    assert data.fmt_money(123456, "acme") == "$1,234.56"
    assert data.fmt_money(123456, "globex") == "1.234,56 USD"
    assert data.fmt_money(123456, "initech") == "USD 1234.56"


# --- admin API ---------------------------------------------------------------

def test_friction_overrides_and_typos(client):
    env_id = make_env(client, difficulty="easy", popups=True)
    assert state(client, env_id)["friction"] == {**PRESETS["easy"].model_dump(), "popups": True}
    resp = client.post("/admin/envs", json={"seed": 1, "friction": {"popup": True}})
    assert resp.status_code == 422


# --- vendor portals ----------------------------------------------------------

def test_portal_requires_login_and_returns_to_requested_page(client):
    env_id = make_env(client)
    resp = client.get(f"/e/{env_id}/acme/invoices?page=2", follow_redirects=False)
    assert resp.status_code == 303 and "/acme/login" in resp.headers["location"]

    bad = client.post(f"/e/{env_id}/acme/login", data={"username": "x", "password": "y"})
    assert bad.status_code == 401 and "Invalid username or password" in bad.text

    ok = login(client, env_id, "acme", next=f"/e/{env_id}/acme/invoices?page=2")
    assert ok.headers["location"] == f"/e/{env_id}/acme/invoices?page=2"


def test_login_ignores_foreign_next_urls(client):
    env_id = make_env(client)
    resp = login(client, env_id, "acme", next="https://evil.test/")
    assert resp.headers["location"].endswith(f"/e/{env_id}/acme/invoices")


def test_easy_portal_is_canonical(client):
    env_id = make_env(client, difficulty="easy")
    login(client, env_id, "globex")
    html = client.get(f"/e/{env_id}/globex/invoices").text
    invoices = state(client, env_id)["invoices"]["globex"]
    assert all(i["number"] in html for i in invoices)  # no pagination
    assert "Invoice Date" in html and "Due Date" in html
    assert "ag-cookie" not in html and "ag-modal" not in html


def test_medium_portal_paginates_and_shows_popups(client):
    env_id = make_env(client, difficulty="medium")
    login(client, env_id, "globex")
    invoices = state(client, env_id)["invoices"]["globex"]
    seen, html = set(), ""
    for page in range(1, 10):
        html = client.get(f"/e/{env_id}/globex/invoices?page={page}").text
        assert 'id="ag-cookie"' in html and 'id="ag-modal"' in html
        seen |= {i["number"] for i in invoices if i["number"] in html}
    assert seen == {i["number"] for i in invoices}
    first = client.get(f"/e/{env_id}/globex/invoices?page=1").text
    assert sum(i["number"] in first for i in invoices) < len(invoices)


def test_renamed_buttons(client):
    env_id = make_env(client, difficulty="easy", renamed_buttons=True)
    page = client.get(f"/e/{env_id}/acme/login").text
    button = re.search(r'<button type="submit">(.*?)</button>', page).group(1)
    assert button in data.BUTTON_VARIANTS["sign_in"]


def test_session_expires_after_max_requests(client):
    env_id = make_env(client, difficulty="easy", session_timeout=True, session_max_requests=3)
    login(client, env_id, "acme")
    for _ in range(3):
        assert client.get(f"/e/{env_id}/acme/invoices", follow_redirects=False).status_code == 200
    expired = client.get(f"/e/{env_id}/acme/invoices", follow_redirects=False)
    assert expired.status_code == 303 and "expired=1" in expired.headers["location"]
    assert "session has expired" in client.get(expired.headers["location"]).text
    login(client, env_id, "acme")
    assert client.get(f"/e/{env_id}/acme/invoices", follow_redirects=False).status_code == 200


def test_detail_page_shows_summary_and_pdf_link(client):
    env_id = make_env(client, difficulty="easy")
    login(client, env_id, "acme")
    inv = state(client, env_id)["invoices"]["acme"][0]
    html = client.get(f"/e/{env_id}/acme/invoices/{inv['number']}").text
    assert data.fmt_money(inv["amount_cents"], "acme") in html
    assert f"/acme/invoices/{inv['number']}/pdf" in html
    assert inv["po_number"] not in html  # only in the PDF


def test_pdf_download(client):
    env_id = make_env(client)
    inv = state(client, env_id)["invoices"]["acme"][0]
    url = f"/e/{env_id}/acme/invoices/{inv['number']}/pdf"
    assert client.get(url, follow_redirects=False).status_code == 303  # login required
    login(client, env_id, "acme")
    resp = client.get(url)
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"] == f'attachment; filename="{inv["number"]}.pdf"'
    assert resp.content.startswith(b"%PDF")


def test_routing_ap_vs_vendor(client):
    env_id = make_env(client)
    assert client.get(f"/e/{env_id}/ap/").status_code == 200
    assert client.get(f"/e/{env_id}/nope/login").status_code == 404
    assert client.get("/e/doesnotexist/acme/login").status_code == 404
