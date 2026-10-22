"""What every agent returns, plus trace bookkeeping shared by both designs."""
from __future__ import annotations

from dataclasses import dataclass, field

from .toolbox import Outcome, Toolbox


@dataclass
class AgentResult:
    answer: str | None
    stop_reason: str  # done | max_steps | stalled | stuck | llm_error
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_seconds: float = 0.0
    trace: list[dict] = field(default_factory=list)
    error: str | None = None
    plan: list[str] = field(default_factory=list)
    replans: int = 0
    failed_actions: int = 0


def record(result: AgentResult, toolbox: Toolbox, step: dict, outcome: Outcome | None) -> None:
    """Append a trace step, with a screenshot of the page after the action."""
    result.steps += 1
    step["step"] = result.steps
    if outcome is not None:
        step["ok"] = outcome.ok
        step["outcome"] = outcome.message[:2000]
        if not outcome.ok:
            result.failed_actions += 1
    if toolbox.trace_dir:
        toolbox.trace_dir.mkdir(parents=True, exist_ok=True)
        shot = toolbox.trace_dir / f"step_{result.steps:03d}.jpg"
        try:
            toolbox.browser.screenshot(str(shot))
            step["screenshot"] = shot.name
        except Exception:  # a screenshot must never fail the run
            pass
    result.trace.append(step)
