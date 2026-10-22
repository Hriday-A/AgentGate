"""Turning finished tasks into lessons.

Failed task  → reflection: the model reads the agent's own trace (actions, outcomes, verifier feedback)
               and writes up to 3 general lessons.
Passed task  → demonstration: the sequence of actions that worked, by element label, no model call.

Leakage guards: the reflection never sees the expected answer or the checker's detail, and any lesson
that contains a long number (an invoice number, an amount) is dropped, so lessons have to be about how
the portals work, not about specific answers.
"""
from __future__ import annotations

import re

from ..suites.invoice import Task
from .memory import ANY, Lesson

REFLECT_SYSTEM = """You review a browser agent's failed attempt at a back-office task and write lessons that
will help it on FUTURE tasks with different invoices.

Rules:
- At most 3 lessons, each one or two sentences, phrased as an instruction ("On the Globex portal, …").
- Be concrete about the interface: button names, where information lives, what blocked progress, what to
  do instead.
- Never include specific invoice numbers, amounts, dates or answers. Those change every time.
- applies_to is "this_vendor" for portal-specific quirks, "all_vendors" for general procedure."""

REFLECT_SCHEMA = {
    "type": "object",
    "properties": {"lessons": {"type": "array", "items": {
        "type": "object",
        "properties": {"lesson": {"type": "string"}, "applies_to": {"type": "string",
                                                                    "enum": ["this_vendor", "all_vendors"]}},
        "required": ["lesson", "applies_to"]}}},
    "required": ["lessons"],
}

FAILURE_TEXT = {
    "wrong_result": "The agent finished, but its result was wrong.",
    "max_steps": "The agent ran out of steps before finishing.",
    "stuck": "The agent gave up after repeated failures and re-planning.",
    "stalled": "The agent stopped calling tools.",
}
SPECIFIC_VALUE = re.compile(r"\d{4,}|\d+[.,]\d{2}\b")  # IDs, amounts, years: signs of a memorized answer


def trace_digest(trace: list[dict], limit: int = 40) -> str:
    lines = []
    for s in [s for s in trace if "action" in s][:limit]:
        target = f" on {s['target']!r}" if s.get("target") else ""
        status = "ok" if s.get("verified", s.get("ok", True)) else "FAILED"
        note = s.get("feedback") or s.get("outcome") or ""
        lines.append(f"#{s['step']} {s['action']}{target} → {status}: {note[:200]}")
    return "\n".join(lines) or "(no actions)"


def reflect(llm, task: Task, vendor_name: str, goal: str, failure: str, trace: list[dict], round_no: int) -> list[Lesson]:
    prompt = (f"Task type: {task.kind} on the {vendor_name} portal ({task.difficulty} difficulty).\n"
              f"Instructions the agent had: {goal}\n\n"
              f"Outcome: {FAILURE_TEXT.get(failure, 'The task failed.')}\n\n"
              f"What the agent did:\n{trace_digest(trace)}")
    resp = llm.extract(REFLECT_SYSTEM, prompt, [], REFLECT_SCHEMA, "lessons")
    lessons = []
    for item in (resp.data or {}).get("lessons", [])[:3]:
        text = str(item.get("lesson", "")).strip()
        if not text or SPECIFIC_VALUE.search(text):
            continue
        vendor = task.vendor if item.get("applies_to") != "all_vendors" else ANY
        lessons.append(Lesson(text, task.kind, vendor, task.difficulty, "reflection", round_no))
    return lessons


def demonstration(task: Task, vendor_name: str, trace: list[dict], round_no: int) -> Lesson | None:
    steps = []
    for s in trace:
        if "action" not in s or not s.get("verified", s.get("ok", True)) or s["action"] in ("step_done", "read_page"):
            continue
        target = f" '{s['target']}'" if s.get("target") else ""
        steps.append(f"{s['action']}{target}")
    if not steps:
        return None
    outline = " → ".join(steps[:25])
    if SPECIFIC_VALUE.search(outline):
        outline = SPECIFIC_VALUE.sub("…", outline)
    return Lesson(f"A successful {task.kind} run on {vendor_name} went: {outline}", task.kind, task.vendor,
                  task.difficulty, "demonstration", round_no)
