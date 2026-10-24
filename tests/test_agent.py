"""Agent tests against the real portals in a real browser, with a scripted model.

The scripted model reads the page snapshot the agent sends it and picks actions
by element label, so these tests exercise the whole loop (snapshot → tool call →
browser action → verification → feedback) without any API key.
"""
import re
import socket
import threading
import time
import urllib.request
import json

import pytest
import uvicorn

from agentgate.agent.browser import Snapshot
from agentgate.agent.graph import GraphAgent, circling, verify
from agentgate.agent.react import ReactAgent
from agentgate.agent.toolbox import Outcome, Toolbox
from agentgate.extraction.extract import extract_invoice
from agentgate.llm import JSONResponse, LLMResponse, ToolCall
from agentgate.portals import data
from agentgate.portals.app import app

from test_extraction import ScriptedLLM, as_extraction

playwright = pytest.importorskip("playwright.sync_api")
ELEMENT = re.compile(r'^\[(\d+)\] (\w+)(?:\[[\w-]+\])? "([^"]*)"(?: -> (\S+))?', re.M)


# --- fixtures ----------------------------------------------------------------

@pytest.fixture(scope="module")
def server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(5)


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def new_env(server, seed=5, difficulty="easy", **friction):
    req = urllib.request.Request(f"{server}/admin/envs", method="POST", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"seed": seed, "difficulty": difficulty, "friction": friction}).encode())
    env = json.load(urllib.request.urlopen(req))
    state = json.load(urllib.request.urlopen(f"{server}/admin/envs/{env['env_id']}/state"))
    return env, state


@pytest.fixture()
def page(browser):
    ctx = browser.new_context(accept_downloads=True)
    yield ctx.new_page()
    ctx.close()


# --- a scripted "model" that drives the portal by reading snapshots ----------

class PortalScript:
    """Answers 'what is the total of invoice X at vendor V' the way a sensible agent would."""

    def __init__(self, vendor_name: str, number: str):
        self.vendor_name, self.number = vendor_name, number
        self.prompts: list[str] = []

    def extract(self, system, prompt, images, schema, name):
        return JSONResponse({"steps": ["Sign in", "Open the invoice", "Report the total"]}, "", 10, 5)

    def complete(self, system, history, tools):
        prompt = history[-1].get("observation") or history[-1]["text"]
        if history[-1].get("observation") is None and history[-1]["role"] == "user":
            prompt = history[-1]["text"]
        self.prompts.append(prompt)
        els = [(int(i), tag, label, href) for i, tag, label, href in ELEMENT.findall(prompt)]

        def find(pred):
            return next((i for i, tag, label, href in els if pred(tag, label, href or "")), None)

        def call(name, **args):
            return LLMResponse("", [ToolCall(f"c{len(self.prompts)}", name, args)], 10, 5)

        if "No thanks" in prompt:  # the survey modal is up
            return call("click", element=find(lambda t, l, h: l == "No thanks"))
        if find(lambda t, l, h: l == "Email") is not None and "value=\"ap-clerk" not in prompt:
            return call("fill", fields=[{"element": find(lambda t, l, h: l == "Email"), "value": data.CREDENTIALS["username"]},
                                        {"element": find(lambda t, l, h: l == "Password"), "value": data.CREDENTIALS["password"]}])
        if find(lambda t, l, h: l == "Email") is not None:
            return call("click", element=find(lambda t, l, h: t == "button"))
        if "Back to list" in prompt:  # on the invoice detail page
            total = re.search(r"Total\t(\S+)", prompt).group(1)
            return call("done", answer=total)
        row_link = find(lambda t, l, h: h.endswith(f"/invoices/{self.number}"))
        if row_link is not None:
            return call("click", element=row_link)
        if find(lambda t, l, h: l == self.vendor_name) is not None:
            return call("click", element=find(lambda t, l, h: l == self.vendor_name))
        return call("go_back")


# --- verify() unit tests -----------------------------------------------------

def snap(text="page", url="http://x/a", alerts=()):
    return Snapshot(url=url, title="t", text=text, elements=[], alerts=list(alerts))


def test_verify_rules():
    click = ToolCall("1", "click", {"element": 1})
    fill = ToolCall("2", "fill", {"fields": []})
    ok = Outcome(True, "ok")
    assert verify(click, ok, snap("a"), snap("b")) == (True, "")
    assert not verify(click, ok, snap("a"), snap("a"))[0]                         # nothing changed
    assert "expired" in verify(click, ok, snap("a"), snap("b", alerts=["Session expired"]))[1]
    assert not verify(click, Outcome(False, "covered"), snap("a"), snap("b"))[0]  # tool failure
    assert verify(fill, ok, snap(alerts=["old error"]), snap("x", alerts=["old error"]))[0]  # stale alert
    assert not verify(fill, ok, snap(), snap(alerts=["new error"]))[0]
    assert not verify(None, ok, snap(), snap())[0]


def test_circling_detects_two_page_ping_pong():
    assert circling(["p1", "p2", "p1", "p2"])
    assert not circling(["p1", "p2", "p1"])
    assert not circling(["list", "inv1", "list", "inv2"])  # back to the list between invoices is fine
    assert not circling(["a", "a", "a", "a"])                # handled by the "changed nothing" rule


# --- end-to-end with a real browser ------------------------------------------

@pytest.mark.parametrize("agent_cls", [GraphAgent, ReactAgent])
def test_agent_completes_lookup_on_easy(server, page, agent_cls):
    env, state = new_env(server)
    inv = state["invoices"]["acme"][2]
    page.goto(env["home"])
    script = PortalScript("Acme Supply Co.", inv["number"])
    result = agent_cls(script, max_steps=12).run("What is the total of the invoice?", Toolbox(page))
    assert result.stop_reason == "done", result.trace
    assert result.answer == data.fmt_money(inv["amount_cents"], "acme")
    if agent_cls is GraphAgent:
        assert result.plan == ["Sign in", "Open the invoice", "Report the total"]
        assert all(s.get("verified", True) for s in result.trace if "action" in s)


def test_graph_agent_recovers_from_blocking_popup(server, page):
    env, state = new_env(server, popups=True)  # survey modal covers the invoice list
    inv = state["invoices"]["acme"][0]
    page.goto(env["home"])
    script = PortalScript("Acme Supply Co.", inv["number"])
    result = GraphAgent(script, max_steps=12).run("What is the total?", Toolbox(page))
    assert result.stop_reason == "done", result.trace
    # Each complete() call yields exactly one action, so prompts and actions line up 1:1.
    actions = [s for s in result.trace if "action" in s]
    dismiss = next(i for i, p in enumerate(script.prompts) if "No thanks" in p)
    assert actions[dismiss]["args"]["element"] is not None and actions[dismiss]["verified"]
    assert all(s.get("verified", True) for s in actions[dismiss:])  # clean from there on


def test_graph_agent_gives_up_cleanly(server, page):
    class Stubborn:  # keeps clicking an element that doesn't exist
        def extract(self, *a):
            return JSONResponse({"steps": ["Do the thing"]}, "", 1, 1)

        def complete(self, *a):
            return LLMResponse("", [ToolCall("x", "click", {"element": 999})], 1, 1)

    env, _ = new_env(server)
    page.goto(env["home"])
    result = GraphAgent(Stubborn(), max_steps=30).run("anything", Toolbox(page))
    assert result.stop_reason == "stuck"
    assert result.replans == 2 and result.steps == 9  # 3 failures × (1 plan + 2 re-plans)


def test_download_extract_and_ap_entry_tools(server, page):
    env, state = new_env(server)
    inv = next(i for i in state["invoices"]["globex"] if not i["scanned"])
    page.goto(f"{env['home']}globex/login")
    tb = Toolbox(page, lambda pdf: extract_invoice(pdf, ScriptedLLM(as_extraction(_invoice(state, inv)))))

    def el(label):
        return next(int(i) for i, _, l, _ in ELEMENT.findall(tb.observe()) if l == label)

    assert tb.run("fill", {"fields": [{"element": el("Email"), "value": data.CREDENTIALS["username"]},
                                      {"element": el("Password"), "value": data.CREDENTIALS["password"]}]}).ok
    assert tb.run("click", {"element": el("Sign in")}).ok
    assert tb.run("navigate", {"url": f"invoices/{inv['number']}"}).ok
    downloaded = tb.run("download", {"element": el("Download PDF")})
    assert downloaded.ok and downloaded.data["filename"] == f"{inv['number']}.pdf"
    extracted = tb.run("extract_invoice", {"filename": f"{inv['number']}.pdf"})
    assert extracted.ok and extracted.data["po_number"] == inv["po_number"]
    assert not tb.run("navigate", {"url": "https://example.com/"}).ok  # other sites refused

    f = extracted.data
    tb.run("navigate", {"url": f"{env['home']}ap/new"})
    filled = tb.run("fill", {"fields": [
        {"element": el("Vendor"), "value": "Globex Industrial GmbH (V-31977)"},
        *({"element": el(label), "value": f[key]} for label, key in [
            ("Invoice number", "invoice_number"), ("Invoice date e.g. YYYY-MM-DD", "invoice_date"),
            ("Due date e.g. YYYY-MM-DD", "due_date"), ("PO number e.g. PO-000000", "po_number"),
            ("Subtotal (USD) e.g. 1234.56", "subtotal"), ("Tax (USD) e.g. 0.00", "tax"),
            ("Shipping (USD) e.g. 0.00", "shipping"), ("Total (USD) e.g. 1234.56", "total")])]})
    assert filled.ok, filled.message
    assert tb.run("click", {"element": el("Save invoice")}).ok
    entries = json.load(urllib.request.urlopen(f"{env['home'].split('/e/')[0]}/admin/envs/{env['env_id']}/state"))["ap_entries"]
    assert [(e["invoice_number"], e["amount_cents"]) for e in entries] == [(inv["number"], inv["amount_cents"])]


def _invoice(state, inv_json) -> data.Invoice:
    """Rebuild the data.Invoice for a state entry (same seed → same objects)."""
    env = data.generate_env("x", state["seed"], None)
    return env.find_invoice(inv_json["vendor"], inv_json["number"])


def test_malformed_tool_arguments_fail_softly(server, page):
    env, _ = new_env(server)
    page.goto(env["home"])
    tb = Toolbox(page)
    tb.observe()
    for name, args in [("fill", {"fields": ["Email", "Password"]}), ("fill", {"fields": "x"}),
                       ("click", "element 3"), ("click", {"element": "abc"})]:
        outcome = tb.run(name, args)
        assert not outcome.ok and outcome.message, (name, args)


def test_graph_agent_notes_survive_and_ping_pong_is_flagged(server, page):
    """A model that flips between list pages gets told it's circling; its notes reach later prompts."""
    env, _ = new_env(server, pagination=True)
    page.goto(f"{env['home']}acme/login")
    tb = Toolbox(page)

    class PingPong:
        def __init__(self):
            self.prompts, self.n = [], 0

        def extract(self, *a):
            return JSONResponse({"steps": ["Read every invoice page"]}, "", 1, 1)

        def complete(self, system, history, tools):
            prompt = history[-1]["text"]
            self.prompts.append(prompt)
            self.n += 1
            if self.n == 1:
                return LLMResponse("", [ToolCall("n", "note", {"text": "page 1 has ACM-1"})], 1, 1)
            if self.n == 2:
                els = {l: int(i) for i, _, l, _ in ELEMENT.findall(prompt)}
                return LLMResponse("", [ToolCall("f", "fill", {"fields": [
                    {"element": els["Email"], "value": data.CREDENTIALS["username"]},
                    {"element": els["Password"], "value": data.CREDENTIALS["password"]}]})], 1, 1)
            if self.n == 3:
                return LLMResponse("", [ToolCall("s", "click", {"element": next(
                    int(i) for i, t, _, _ in ELEMENT.findall(prompt) if t == "button")})], 1, 1)
            target = "invoices?page=2" if self.n % 2 else "invoices?page=1"
            return LLMResponse("", [ToolCall("g", "navigate", {"url": target})], 1, 1)

    model = PingPong()
    result = GraphAgent(model, max_steps=12).run("Sum the unpaid invoices", tb)
    assert all("page 1 has ACM-1" in p for p in model.prompts[1:])
    flagged = [s for s in result.trace if "back and forth" in s.get("feedback", "")]
    assert flagged and result.replans >= 1


def test_graph_agent_flags_an_exact_repeat_that_changes_nothing(server, page):
    class Repeater:  # reads the same page over and over
        def __init__(self):
            self.prompts = []

        def extract(self, *a):
            return JSONResponse({"steps": ["Look around"]}, "", 1, 1)

        def complete(self, system, history, tools):
            self.prompts.append(history[-1]["text"])
            return LLMResponse("", [ToolCall("r", "read_page", {})], 1, 1)

    env, _ = new_env(server)
    page.goto(env["home"])
    model = Repeater()
    result = GraphAgent(model, max_steps=8).run("anything", Toolbox(page))
    actions = [s for s in result.trace if "action" in s]
    assert actions[0]["verified"] and not actions[1]["verified"]
    assert "exactly this" in actions[1]["feedback"] and result.replans >= 1


def test_graph_agent_nudges_after_many_actions_on_one_step(server, page):
    class Lingerer:  # keeps succeeding without ever marking the step done
        def __init__(self):
            self.prompts = []

        def extract(self, *a):
            return JSONResponse({"steps": ["Look around"]}, "", 1, 1)

        def complete(self, system, history, tools):
            self.prompts.append(history[-1]["text"])
            return LLMResponse("", [ToolCall("n", "note", {"text": f"fact {len(self.prompts)}"})], 1, 1)

    env, _ = new_env(server)
    page.goto(env["home"])
    model = Lingerer()
    GraphAgent(model, max_steps=8).run("anything", Toolbox(page))
    assert not any("actions on the current plan step" in p for p in model.prompts[:6])
    assert "6 actions on the current plan step" in model.prompts[6]
