"""Baseline agent: a single ReAct-style loop. Observe the page, pick one tool, repeat.

No plan, no verification beyond what the tool itself reports. It exists as the
control group for the planner/executor graph in graph.py.
"""
from __future__ import annotations

import time

from .base import AgentResult, record
from .toolbox import BROWSER_TOOLS, DONE, Toolbox

SYSTEM_PROMPT = """You are a careful back-office assistant operating a web browser to complete a task.

Each turn you see the current page: its URL, visible text, and a numbered list of interactive elements.
Act with the tools, referring to elements by their number from the LATEST snapshot only.
Call exactly one tool per turn.

Guidelines:
- Cookie banners and pop-ups can block clicks. Dismiss them when they get in the way.
- Lists may be paginated. Check every page before concluding something is missing.
- Invoice details such as PO number, subtotal, tax and shipping are in the invoice PDF: download it, then
  use extract_invoice.
- When the task is complete, call `done`. For questions, put only the requested value in `answer`."""

TOOLS = BROWSER_TOOLS + [DONE]
OMITTED = "(older page snapshot omitted)"


def _compact(history: list[dict]) -> list[dict]:
    """Keep only the newest page snapshot in the prompt."""
    last = max((i for i, m in enumerate(history) if m.get("observation")), default=-1)
    return [{**m, "observation": OMITTED} if m.get("observation") and i != last else m
            for i, m in enumerate(history)]


class ReactAgent:
    name = "react"

    def __init__(self, llm, max_steps: int = 30):
        self.llm = llm
        self.max_steps = max_steps

    def run(self, goal: str, toolbox: Toolbox) -> AgentResult:
        result = AgentResult(answer=None, stop_reason="max_steps")
        history: list[dict] = [{"role": "user", "text": f"Task: {goal}", "observation": toolbox.observe()}]
        nudges = 0

        while result.steps < self.max_steps:
            t0 = time.perf_counter()
            try:
                resp = self.llm.complete(SYSTEM_PROMPT, _compact(history), TOOLS)
            except Exception as e:
                result.stop_reason, result.error = "llm_error", f"{type(e).__name__}: {e}"
                break
            result.llm_seconds += time.perf_counter() - t0
            result.input_tokens += resp.input_tokens
            result.output_tokens += resp.output_tokens
            history.append({"role": "assistant", "text": resp.text, "tool_calls": resp.tool_calls})

            if not resp.tool_calls:
                nudges += 1
                if nudges > 2:
                    result.stop_reason = "stalled"
                    break
                history.append({"role": "user", "text": "Use one of the tools. Call `done` when the task is finished."})
                continue

            # One action per turn: later calls would reference stale element numbers.
            call, extra = resp.tool_calls[0], resp.tool_calls[1:]
            step = {"url": toolbox.page.url, "action": call.name, "args": call.args, "thought": resp.text}
            if call.name == "done":
                result.answer, result.stop_reason = str(call.args.get("answer", "")), "done"
                record(result, toolbox, step, None)
                break
            outcome = toolbox.run(call.name, call.args)
            record(result, toolbox, step, outcome)
            results = [{"id": call.id, "content": outcome.message}]
            results += [{"id": c.id, "content": "Not executed: only one action per turn."} for c in extra]
            history.append({"role": "tool", "results": results, "observation": toolbox.observe()})

        return result
