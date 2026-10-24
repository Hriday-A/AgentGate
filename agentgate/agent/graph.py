"""Planner/executor agent built as a LangGraph state machine.

            ┌────────┐
  START ──► │  plan  │ ◄──────────────── 3 failures in a row (re-plan, at most 2×)
            └───┬────┘                                   │
                ▼                                        │
            ┌────────┐   ┌───────┐   ┌────────┐          │
            │execute │──►│  act  │──►│ verify │──────────┤
            └────────┘   └───────┘   └────────┘          │
                ▲                                        │
                └──────── ok, or failed < 3 times ───────┘   done / out of steps / stuck → END

plan     LLM writes a short numbered plan for the task (or for what's left of it).
execute  LLM picks ONE tool call for the current plan step, seeing the plan, recent actions,
         its notes, the last verification feedback and the current page. Each call starts
         fresh (no chat history), so `note` is how facts survive leaving a page.
act      Runs the tool. `step_done` advances the plan; `done` ends the run.
verify   Deterministic checks, no LLM: did the tool report failure? did the page show an
         error alert (bad login, expired session, form validation)? did a click change nothing?
         is the agent bouncing between the same two pages?
         Failures are fed back to `execute` as feedback; repeated failures trigger a re-plan.
"""
from __future__ import annotations

import json
import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..llm import ToolCall
from ..telemetry import tracer
from .base import AgentResult, record
from .browser import Snapshot
from .toolbox import BROWSER_TOOLS, DONE, Outcome, Toolbox, _tool

PLAN_SYSTEM = """You plan browser tasks for a back-office assistant. Write a short plan of 3–8 concrete steps.
Each step should be checkable, e.g. "Sign in to the Acme Supply portal", "Open invoice ACM-12345",
"Download the invoice PDF", "Extract the invoice fields", "Fill and submit the AP form".
Invoice details such as PO number, subtotal, tax and shipping are only in the invoice PDF."""

EXEC_SYSTEM = """You are a careful back-office assistant operating a web browser, working through a plan.

Each turn, call exactly ONE tool for the current plan step. Refer to elements by their number in the
CURRENT page snapshot only. When the current step is complete, call `step_done`. When the whole task is
complete, call `done` (for questions, `answer` holds only the requested value).

Guidelines:
- Cookie banners and pop-ups block clicks. Dismiss them first.
- Lists may be paginated. Check every page before concluding something is missing. Before leaving a
  page, `note` what you need from it: your notes are shown every turn, pages you left are not.
- If your session expires, sign in again and continue.
- Use `fill` to set several form fields in one action.
- Copy extracted values exactly; the AP form wants dates as YYYY-MM-DD and plain-number amounts."""

PLAN_SCHEMA = {"type": "object", "properties": {"steps": {"type": "array", "items": {"type": "string"}}},
               "required": ["steps"]}
STEP_DONE = _tool("step_done", "Mark the current plan step as completed.",
                  {"note": {"type": "string", "description": "What was achieved"}})
NOTE = _tool("note", "Write down facts you will need later, e.g. the unpaid invoices on this page.",
             {"text": {"type": "string"}}, ["text"])
TOOLS = BROWSER_TOOLS + [NOTE, STEP_DONE, DONE]

MAX_FAILURES = 3   # consecutive failed actions before re-planning
MAX_REPLANS = 2


class State(TypedDict, total=False):
    goal: str
    hints: str             # lessons retrieved from memory, already formatted
    plan: list[str]
    step_idx: int
    call: ToolCall | None
    thought: str
    before: Snapshot | None
    outcome: Outcome | None
    feedback: str
    failures: int          # consecutive failed actions
    replans: int
    actions: list[str]     # one-line summaries of past actions
    notes: list[str]       # facts the executor chose to keep (see the `note` tool)
    visited: list[str]     # page fingerprint after each action
    step_actions: int      # actions taken since the current plan step began
    last_call: tuple[str, str] | None  # (tool, args) of the previous action, to catch exact repeats
    truncated: bool        # the last reply hit the output cap before calling a tool
    stop: str | None       # set when the run should end


def verify(call: ToolCall | None, outcome: Outcome, before: Snapshot | None, after: Snapshot) -> tuple[bool, str]:
    """Rule-based check of one action. Returns (ok, feedback for the executor)."""
    if call is None:
        return False, "You did not call a tool. Call exactly one tool."
    if not outcome.ok:
        return False, outcome.message
    if call.name in ("click", "navigate"):
        # A click may submit a form or navigate, so any error alert on the resulting page counts.
        if after.alerts:
            return False, "The page now shows: " + " | ".join(after.alerts)
        if call.name == "click" and before is not None and before.fingerprint == after.fingerprint:
            return False, ("That click changed nothing on the page. Something may be covering it, or it is "
                           "not the right element.")
    elif before is not None:
        new_alerts = [a for a in after.alerts if a not in before.alerts]
        if new_alerts:
            return False, "The page now shows: " + " | ".join(new_alerts)
    return True, ""


def circling(visited: list[str]) -> bool:
    """True when the last four pages go A, B, A, B: the agent is bouncing between two pages."""
    if len(visited) < 4:
        return False
    a, b, c, d = visited[-4:]
    return a == c and b == d and a != b


def _plan_text(plan: list[str], idx: int) -> str:
    lines = []
    for i, step in enumerate(plan):
        mark = "✓" if i < idx else ("→" if i == idx else " ")
        lines.append(f"  {mark} {i + 1}. {step}" + ("   (current)" if i == idx else ""))
    return "\n".join(lines) or "  (no plan)"


class GraphAgent:
    name = "graph"

    def __init__(self, llm, max_steps: int = 30):
        self.llm = llm
        self.max_steps = max_steps
        self.graph = self._build()
        self.toolbox: Toolbox | None = None
        self.result: AgentResult | None = None

    # --- wiring ----------------------------------------------------------------

    def _build(self):
        g = StateGraph(State)
        for name in ("plan", "execute", "act", "verify"):
            g.add_node(name, self._traced(name, getattr(self, name)))
        g.add_edge(START, "plan")
        g.add_conditional_edges("plan", lambda s: END if s.get("stop") else "execute", ["execute", END])
        g.add_conditional_edges("execute", lambda s: END if s.get("stop") else "act", ["act", END])
        g.add_edge("act", "verify")
        g.add_conditional_edges("verify", self.route, ["plan", "execute", END])
        return g.compile()

    @staticmethod
    def _traced(name, fn):
        """Wrap a node so each visit shows up as an OpenTelemetry span."""
        def node(state: State) -> State:
            with tracer.start_as_current_span(f"agent.{name}"):
                return fn(state)
        return node

    def route(self, state: State) -> str:
        if state.get("stop"):
            return END
        if state.get("failures", 0) >= MAX_FAILURES:
            return "plan"
        return "execute"

    def run(self, goal: str, toolbox: Toolbox, hints: list[str] | None = None) -> AgentResult:
        self.toolbox = toolbox
        self.result = AgentResult(answer=None, stop_reason="max_steps")
        formatted = ("\n\nLessons from previous runs (apply them when relevant):\n" +
                     "\n".join(f"- {h}" for h in hints)) if hints else ""
        # Each action passes through 3 nodes; leave headroom for plan nodes.
        limit = self.max_steps * 3 + (MAX_REPLANS + 1) * 2 + 10
        final = self.graph.invoke({"goal": goal, "hints": formatted, "plan": [], "step_idx": 0, "failures": 0,
                                   "replans": 0, "actions": [], "notes": [], "visited": [], "feedback": "",
                                   "stop": None},
                                  {"recursion_limit": limit})
        self.result.stop_reason = final.get("stop") or "max_steps"
        self.result.plan = final.get("plan", [])
        self.result.replans = final.get("replans", 0)
        return self.result

    # --- nodes -----------------------------------------------------------------

    def _llm_call(self, fn, *args):
        t0 = time.perf_counter()
        resp = fn(*args)
        self.result.llm_seconds += time.perf_counter() - t0
        self.result.input_tokens += resp.input_tokens
        self.result.output_tokens += resp.output_tokens
        return resp

    def plan(self, state: State) -> State:
        replanning = bool(state.get("plan"))
        if replanning and state.get("replans", 0) >= MAX_REPLANS:
            return {"stop": "stuck"}
        done_steps = state["plan"][: state["step_idx"]] if replanning else []
        prompt = f"Task: {state['goal']}{state.get('hints', '')}\n\nCurrent page:\n{self.toolbox.observe()}"
        if replanning:
            prompt += ("\n\nSteps already completed:\n" + "\n".join(f"- {s}" for s in done_steps) +
                       f"\n\nThe step \"{state['plan'][state['step_idx']]}\" failed repeatedly. Last error: "
                       f"{state.get('feedback', '')}\nWrite a new plan for the remaining work only, taking a "
                       "different approach where needed.")
        try:
            resp = self._llm_call(self.llm.extract, PLAN_SYSTEM, prompt, [], PLAN_SCHEMA, "plan")
        except Exception as e:
            self.result.error = f"{type(e).__name__}: {e}"
            return {"stop": "llm_error"}
        steps = [str(s) for s in (resp.data or {}).get("steps", []) if str(s).strip()] or [state["goal"]]
        self.result.trace.append({"event": "replan" if replanning else "plan", "plan": steps})
        return {"plan": done_steps + steps, "step_idx": len(done_steps), "failures": 0, "step_actions": 0,
                "replans": state.get("replans", 0) + (1 if replanning else 0)}

    def execute(self, state: State) -> State:
        if self.result.steps >= self.max_steps:
            return {"stop": "max_steps"}
        before = self.toolbox.browser.snapshot()
        notes = "".join(f"\n  - {n}" for n in state.get("notes", []))
        notes = f"\n\nYour notes:{notes}" if notes else ""
        if self.toolbox.extractions:
            notes += "\n\nExtracted invoice data:\n" + json.dumps(self.toolbox.extractions, indent=1)
        recent = "\n".join(f"  - {a}" for a in state.get("actions", [])[-6:]) or "  (none yet)"
        feedback = f"\n\n⚠ Your last action failed: {state['feedback']}" if state.get("feedback") else ""
        on_step = state.get("step_actions", 0)
        if on_step >= 6:
            feedback += (f"\n\nYou have taken {on_step} actions on the current plan step. If it is already "
                         "complete, call `step_done`.")
        prompt = (f"Task: {state['goal']}{state.get('hints', '')}\n\nPlan:\n{_plan_text(state['plan'], state['step_idx'])}"
                  f"\n\nRecent actions:\n{recent}{notes}{feedback}\n\nCurrent page:\n{before.render()}")
        try:
            resp = self._llm_call(self.llm.complete, EXEC_SYSTEM, [{"role": "user", "text": prompt}], TOOLS)
        except Exception as e:
            self.result.error = f"{type(e).__name__}: {e}"
            return {"stop": "llm_error"}
        return {"call": resp.tool_calls[0] if resp.tool_calls else None, "thought": resp.text, "before": before,
                "truncated": getattr(resp, "truncated", False)}

    def act(self, state: State) -> State:
        call = state.get("call")
        step = {"url": self.toolbox.page.url, "action": call.name if call else None,
                "args": call.args if call else None, "thought": state.get("thought", ""),
                "plan_step": state["step_idx"] + 1}
        if call is None:
            outcome = Outcome(False, "No tool call.")
        elif call.name == "done":
            self.result.answer = str(call.args.get("answer", ""))
            record(self.result, self.toolbox, step, None)
            return {"stop": "done", "outcome": Outcome(True, "finished")}
        elif call.name == "note":
            outcome = Outcome(True, "Noted.")
        elif call.name == "step_done":
            outcome = Outcome(True, f"Step {state['step_idx'] + 1} marked done. {call.args.get('note', '')}".strip())
        else:
            outcome = self.toolbox.run(call.name, call.args)
        record(self.result, self.toolbox, step, outcome)
        return {"outcome": outcome}

    def verify(self, state: State) -> State:
        if state.get("stop"):
            return {}
        call, outcome = state.get("call"), state["outcome"]
        after = self.toolbox.browser.snapshot()
        ok, feedback = verify(call, outcome, state.get("before"), after)
        if call is None and state.get("truncated"):
            feedback = "Your reply was cut off before you called a tool. Think briefly, then call one tool."
        last = state.get("last_call")
        if ok and call and last == (call.name, json.dumps(call.args, sort_keys=True)) and \
                state.get("before") is not None and state["before"].fingerprint == after.fingerprint:
            ok, feedback = False, (f"You just did exactly this and nothing changed. If the current step is complete, "
                                   f"call `step_done`; otherwise do the next thing (e.g. after `download`, "
                                   f"`extract_invoice`).")
        visited = state.get("visited", [])
        if call and call.name in ("click", "navigate", "go_back"):
            visited = visited + [after.fingerprint]
            if ok and circling(visited):
                ok, feedback = False, ("You are going back and forth between the same two pages. Use `note` to "
                                       "record what you need from each page, then move on.")
        if hasattr(self.llm, "feedback"):
            self.llm.feedback(ok)  # lets a learning router score the model that chose this action
        self.result.trace[-1]["verified"] = ok
        if feedback:
            self.result.trace[-1]["feedback"] = feedback
        summary = (f"{call.name} {json.dumps(call.args)[:120]} → {'ok' if ok else 'FAILED'}: "
                   f"{(outcome.message if ok else feedback)[:160]}") if call else "no tool call → FAILED"
        update: State = {"actions": state.get("actions", []) + [summary], "feedback": "" if ok else feedback,
                         "visited": visited,
                         "last_call": (call.name, json.dumps(call.args, sort_keys=True)) if call else None,
                         "failures": 0 if ok else state.get("failures", 0) + 1}
        if ok and call and call.name == "note":
            update["notes"] = state.get("notes", []) + [str(call.args.get("text", ""))[:500]]
        update["step_actions"] = state.get("step_actions", 0) + 1
        if ok and call and call.name == "step_done":
            update["step_actions"] = 0
            update["step_idx"] = min(state["step_idx"] + 1, len(state["plan"]))
        if self.result.steps >= self.max_steps:
            update["stop"] = "max_steps"
        return update
