"""The reference agent: an LLM choosing one browser action per turn."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Page

from .browser import Browser

SYSTEM_PROMPT = """You are a careful back-office assistant operating a web browser to complete a task.

Each turn you see the current page: its URL, visible text, and a numbered list of interactive elements.
Act with the tools, referring to elements by their number from the LATEST snapshot only.
Call exactly one tool per turn.

Guidelines:
- Cookie banners and pop-ups can block clicks. Dismiss them when they get in the way.
- Lists may be paginated. Check every page before concluding something is missing.
- Read values exactly as shown, then convert formats only where the destination requires it.
- When the task is complete, call `done`. For questions, put only the requested value in `answer`.
  For data-entry tasks, confirm what you recorded."""

TOOLS = [
    {
        "name": "click",
        "description": "Click a link, button or other element.",
        "input_schema": {"type": "object", "properties": {"element": {"type": "integer"}}, "required": ["element"]},
    },
    {
        "name": "type",
        "description": "Replace the contents of a text input with the given text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "element": {"type": "integer"},
                "text": {"type": "string"},
                "press_enter": {"type": "boolean", "description": "Press Enter after typing (submits forms)."},
            },
            "required": ["element", "text"],
        },
    },
    {
        "name": "select",
        "description": "Choose an option in a dropdown by its visible label.",
        "input_schema": {
            "type": "object",
            "properties": {"element": {"type": "integer"}, "option": {"type": "string"}},
            "required": ["element", "option"],
        },
    },
    {
        "name": "go_back",
        "description": "Go back to the previous page.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "done",
        "description": "Finish the task and report the final answer.",
        "input_schema": {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]},
    },
]

OMITTED = "(older page snapshot omitted)"


@dataclass
class AgentResult:
    answer: str | None
    stop_reason: str  # done | max_steps | stalled | llm_error
    steps: int
    input_tokens: int = 0
    output_tokens: int = 0
    llm_seconds: float = 0.0
    trace: list[dict] = field(default_factory=list)
    error: str | None = None


def _compact(history: list[dict]) -> list[dict]:
    """Keep only the newest page snapshot in the prompt."""
    last = max((i for i, m in enumerate(history) if m.get("observation")), default=-1)
    return [
        {**m, "observation": OMITTED} if m.get("observation") and i != last else m
        for i, m in enumerate(history)
    ]


class Agent:
    def __init__(self, llm, max_steps: int = 30):
        self.llm = llm
        self.max_steps = max_steps

    def run(self, goal: str, page: Page, trace_dir: Path | None = None) -> AgentResult:
        browser = Browser(page)
        if trace_dir:
            trace_dir.mkdir(parents=True, exist_ok=True)
        result = AgentResult(answer=None, stop_reason="max_steps", steps=0)
        history: list[dict] = [{"role": "user", "text": f"Task: {goal}", "observation": browser.observe()}]
        nudges = 0

        for turn in range(self.max_steps):
            t0 = time.perf_counter()
            try:
                resp = self.llm.complete(SYSTEM_PROMPT, _compact(history), TOOLS)
            except Exception as e:  # network, auth, rate limit…
                result.stop_reason, result.error = "llm_error", f"{type(e).__name__}: {e}"
                break
            llm_s = time.perf_counter() - t0
            result.llm_seconds += llm_s
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

            # One action per turn: later calls would reference stale element numbers
            # once the first one navigates.
            call, extra = resp.tool_calls[0], resp.tool_calls[1:]
            result.steps += 1
            step = {"step": result.steps, "turn": turn, "url": page.url, "action": call.name, "args": call.args,
                    "thought": resp.text, "llm_seconds": round(llm_s, 3)}
            if call.name == "done":
                result.answer = str(call.args.get("answer", ""))
                result.stop_reason = "done"
                step["outcome"] = "finished"
                result.trace.append(step)
                break
            step["outcome"] = browser.execute(call.name, call.args)
            if trace_dir:
                shot = trace_dir / f"step_{result.steps:03d}.jpg"
                browser.screenshot(str(shot))
                step["screenshot"] = shot.name
            result.trace.append(step)
            # Every tool call needs a matching result.
            results = [{"id": call.id, "content": step["outcome"]}]
            results += [{"id": c.id, "content": "Not executed: only one action per turn is allowed."} for c in extra]
            history.append({"role": "tool", "results": results, "observation": browser.observe()})

        return result
