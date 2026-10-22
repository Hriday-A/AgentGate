"""Oracle tests: a perfect agent passes every task, and wrong answers fail."""
import pytest

from agentgate.portals import data
from agentgate.suites import invoice

TASKS = invoice.load_tasks()


def test_suite_has_54_unique_tasks():
    assert len(TASKS) == 54
    assert len({t.id for t in TASKS}) == 54
    assert {t.difficulty for t in TASKS} == {"easy", "medium", "hard"}
    smoke = [t for t in TASKS if t.smoke]
    assert len({t.kind for t in smoke}) == len(smoke) == 6  # one smoke task per kind


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_oracle_passes_and_wrong_answer_fails(task):
    state = data.generate_env("t", task.seed).to_json()
    prepared = invoice.prepare(task, state)
    assert "username" in prepared.prompt

    if task.kind.startswith("enter_"):
        inv = prepared.expected
        good = {**state, "ap_entries": [{
            "vendor": task.vendor, "invoice_number": inv["number"], "invoice_date": inv["issue_date"],
            "due_date": inv["due_date"], "po_number": inv["po_number"], "subtotal_cents": inv["subtotal_cents"],
            "tax_cents": inv["tax_cents"], "shipping_cents": inv["shipping_cents"],
            "amount_cents": inv["amount_cents"],
        }]}
        assert prepared.check(good, "done") == (True, "entry matches")
        assert all(prepared.fields(good).values())
        bad_amount = {**good, "ap_entries": [{**good["ap_entries"][0], "amount_cents": inv["amount_cents"] + 1}]}
        assert not prepared.check(bad_amount, "done")[0]
        assert not prepared.check(state, "done")[0]  # nothing recorded
        return

    if task.kind in ("lookup_amount", "total_outstanding"):
        cents = prepared.expected
        right = f"${cents // 100:,}.{cents % 100:02d}"
        wrong = f"{cents // 100 + 1}.{cents % 100:02d}"
    elif task.kind == "lookup_due_date":
        right, wrong = prepared.expected, "1999-01-01"
    else:
        right, wrong = str(prepared.expected), str(prepared.expected + 1)

    assert prepared.check(state, right)[0], prepared.check(state, right)
    assert not prepared.check(state, wrong)[0]
    assert not prepared.check(state, None)[0]


def test_parse_money_handles_portal_formats():
    assert invoice.parse_money("$1,234.56") == invoice.parse_money("1234.56")
    assert invoice.parse_money("The total is 98.10 USD") is not None
    assert invoice.parse_money("no idea") is None
