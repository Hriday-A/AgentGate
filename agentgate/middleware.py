"""Layers around a model backend: metering, caching and routing.

    build_llm(...) returns   Metered( Routed( Cached(small), Cached(large) ) )
                         or  Metered( Cached(large) )            without routing

Metered  records every call in a ledger (model, tokens, latency, cost, cache hit) and an OpenTelemetry span.
Cached   answers identical requests from SQLite. The environment is deterministic, so re-running a task
         replays the same pages and the same prompts.
Routed   sends easy steps (sign in, dismiss a pop-up, navigate, download) to a small local model and hard
         ones (planning, forms, extraction, anything after a failure) to the large model. If the small
         model doesn't produce a tool call, the call escalates to the large one.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import random
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .llm import JSONResponse, LLMResponse, ToolCall, cost_usd, make_llm
from .telemetry import tracer

# --- metering ----------------------------------------------------------------


@dataclass
class Call:
    kind: str           # complete | extract
    model: str          # provider:model that answered
    route: str | None   # small | large | None (no routing)
    cached: bool
    input_tokens: int
    output_tokens: int
    seconds: float
    cost_usd: float | None


@dataclass
class Ledger:
    calls: list[Call] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, call: Call) -> None:
        with self.lock:
            self.calls.append(call)

    def summary(self) -> dict:
        with self.lock:
            calls = list(self.calls)
        costs = [c.cost_usd for c in calls if not c.cached]
        return {
            "llm_calls": len(calls),
            "cached_calls": sum(c.cached for c in calls),
            "small_calls": sum(c.route == "small" for c in calls),
            "input_tokens": sum(c.input_tokens for c in calls if not c.cached),
            "output_tokens": sum(c.output_tokens for c in calls if not c.cached),
            "llm_seconds": round(sum(c.seconds for c in calls), 2),
            "cost_usd": None if any(c is None for c in costs) else round(sum(costs), 6),
            "by_model": {m: sum(c.model == m for c in calls) for m in sorted({c.model for c in calls})},
        }


def _cost(model: str, tin: int, tout: int) -> float | None:
    provider, _, name = model.partition(":")
    return cost_usd(provider, name, tin, tout)


class MeteredLLM:
    """Outermost layer: every call is timed, priced and traced."""

    def __init__(self, inner, ledger: Ledger | None = None):
        self.inner = inner
        self.ledger = ledger or Ledger()
        self.provider = getattr(inner, "provider", "?")
        self.model = getattr(inner, "model", "?")

    def _record(self, kind: str, fn, *args):
        with tracer.start_as_current_span(f"llm.{kind}") as span:
            t0 = time.perf_counter()
            resp = fn(*args)
            seconds = time.perf_counter() - t0
            route = getattr(self.inner, "last_route", None)
            model = resp.model or f"{self.provider}:{self.model}"
            self.ledger.add(Call(kind, model, route, resp.cached, resp.input_tokens, resp.output_tokens, seconds,
                                 0.0 if resp.cached else _cost(model, resp.input_tokens, resp.output_tokens)))
            span.set_attributes({"gen_ai.request.model": model, "gen_ai.usage.input_tokens": resp.input_tokens,
                                 "gen_ai.usage.output_tokens": resp.output_tokens, "agentgate.cached": resp.cached,
                                 "agentgate.route": route or "none"})
            return resp

    def complete(self, system: str, history: list[dict], tools: list[dict]) -> LLMResponse:
        return self._record("complete", self.inner.complete, system, history, tools)

    def extract(self, system: str, prompt: str, images: list[bytes], schema: dict, name: str) -> JSONResponse:
        return self._record("extract", self.inner.extract, system, prompt, images, schema, name)

    def feedback(self, ok: bool) -> None:
        if hasattr(self.inner, "feedback"):
            self.inner.feedback(ok)


# --- caching -----------------------------------------------------------------


def _key(*parts) -> str:
    def default(o):
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        if isinstance(o, bytes):
            return hashlib.sha256(o).hexdigest()
        raise TypeError(type(o))
    return hashlib.sha256(json.dumps(parts, default=default, sort_keys=True).encode()).hexdigest()


class CachedLLM:
    """Exact-match response cache in SQLite, keyed by model + full request."""

    def __init__(self, inner, path: str | Path = ".cache/llm.sqlite"):
        self.inner = inner
        self.provider, self.model = inner.provider, inner.model
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("create table if not exists responses (key text primary key, body text not null)")
        self.lock = threading.Lock()

    def _get(self, key: str) -> dict | None:
        with self.lock:
            row = self.db.execute("select body from responses where key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _put(self, key: str, body: dict) -> None:
        with self.lock:
            self.db.execute("insert or replace into responses values (?, ?)", (key, json.dumps(body)))
            self.db.commit()

    def complete(self, system: str, history: list[dict], tools: list[dict]) -> LLMResponse:
        key = _key("complete", self.provider, self.model, system, history, tools)
        if (hit := self._get(key)) is not None:
            return LLMResponse(hit["text"], [ToolCall(**c) for c in hit["tool_calls"]], model=hit["model"], cached=True)
        resp = self.inner.complete(system, history, tools)
        if resp.tool_calls:  # don't cache non-answers; a retry deserves a fresh attempt
            self._put(key, {"text": resp.text, "tool_calls": [dataclasses.asdict(c) for c in resp.tool_calls],
                            "model": resp.model})
        return resp

    def extract(self, system: str, prompt: str, images: list[bytes], schema: dict, name: str) -> JSONResponse:
        key = _key("extract", self.provider, self.model, system, prompt, images, schema, name)
        if (hit := self._get(key)) is not None:
            return JSONResponse(hit["data"], hit["text"], model=hit["model"], cached=True)
        resp = self.inner.extract(system, prompt, images, schema, name)
        if resp.data is not None:
            self._put(key, {"data": resp.data, "text": resp.text, "model": resp.model})
        return resp


# --- routing -----------------------------------------------------------------

STEP_PATTERNS = [  # first match wins
    ("login", r"\bsign[ -]?in\b|\blog[ -]?in\b|credentials"),
    ("popup", r"dismiss|cookie|pop-?up|banner|survey|modal"),
    ("download", r"download"),
    ("extract", r"extract"),
    ("form", r"\bfill\b|\benter\b|record|submit|form|accounts payable|\bap\b"),
    ("answer", r"report|answer|total|count|sum|calculate|tell"),
    ("navigate", r"open|navigate|go to|find|browse|click|page|list|portal"),
]
EASY_STEPS = {"login", "popup", "navigate", "download"}


def classify(history: list[dict]) -> tuple[str, bool]:
    """(step type, whether the last action failed) from the prompt the agent is about to send."""
    last = history[-1]
    text = f"{last.get('text', '')}\n{last.get('observation', '')}"
    if last.get("role") == "tool":
        text += "\n".join(r["content"] for r in last.get("results", []))
    failed = "Your last action failed" in text or "Action failed" in text
    m = re.search(r"→ \d+\. (.+?)\s+\(current\)", text)
    subject = m.group(1) if m else text[:400]  # graph agent: the current plan step; react: the task itself
    for kind, pattern in STEP_PATTERNS:
        if re.search(pattern, subject, re.I):
            return kind, failed
    return "other", failed


class RulePolicy:
    name = "rules"

    def choose(self, step: str, failed: bool) -> str:
        return "small" if step in EASY_STEPS and not failed else "large"

    def update(self, context: str, arm: str, reward: float) -> None:
        pass


class BanditPolicy:
    """Epsilon-greedy contextual bandit over {small, large}, one value estimate per (step type, failed).

    Reward = 1 if the action then passed verification, else 0, minus a latency penalty, so the small
    model wins a context only if it is about as reliable there and actually faster.
    """
    name = "bandit"

    def __init__(self, path: str | Path = ".cache/bandit.json", epsilon: float = 0.1, seed: int = 0):
        self.path = Path(path)
        self.epsilon = epsilon
        self.rng = random.Random(seed)
        self.stats: dict[str, dict[str, list[float]]] = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.lock = threading.Lock()

    def _mean(self, context: str, arm: str) -> float:
        n, total = self.stats.get(context, {}).get(arm, [0, 0.0])
        return total / n if n else 1.0  # optimistic start: try each arm before trusting the estimate

    def choose(self, step: str, failed: bool) -> str:
        context = f"{step}:{'fail' if failed else 'ok'}"
        if self.rng.random() < self.epsilon:
            return self.rng.choice(["small", "large"])
        return max(["small", "large"], key=lambda arm: self._mean(context, arm))

    def update(self, context: str, arm: str, reward: float) -> None:
        with self.lock:
            n, total = self.stats.setdefault(context, {}).get(arm, [0, 0.0])
            self.stats[context][arm] = [n + 1, total + reward]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.stats, indent=1))


class RoutedLLM:
    LATENCY_PENALTY = 0.02  # reward lost per second of model latency

    def __init__(self, small, large, policy):
        self.small, self.large, self.policy = small, large, policy
        self.provider, self.model = "routed", f"{small.model}|{large.model}"
        self.last_route: str | None = None
        self._pending: tuple[str, str, float] | None = None  # (context, arm, seconds) awaiting verification

    def complete(self, system: str, history: list[dict], tools: list[dict]) -> LLMResponse:
        step, failed = classify(history)
        arm = self.policy.choose(step, failed)
        t0 = time.perf_counter()
        resp = (self.small if arm == "small" else self.large).complete(system, history, tools)
        if arm == "small" and not resp.tool_calls:
            # Cascade: a small model that can't produce an action hands the step to the large one.
            self.policy.update(f"{step}:{'fail' if failed else 'ok'}", "small", 0.0)
            arm = "large"
            resp = self.large.complete(system, history, tools)
        self.last_route = arm
        self._pending = None if resp.cached else (f"{step}:{'fail' if failed else 'ok'}", arm, time.perf_counter() - t0)
        return resp

    def extract(self, system: str, prompt: str, images: list[bytes], schema: dict, name: str) -> JSONResponse:
        self.last_route = "large"  # planning and document extraction always go to the large model
        return self.large.extract(system, prompt, images, schema, name)

    def feedback(self, ok: bool) -> None:
        """Called by the agent after verifying the action this call produced."""
        if self._pending:
            context, arm, seconds = self._pending
            self.policy.update(context, arm, (1.0 if ok else 0.0) - self.LATENCY_PENALTY * seconds)
            self._pending = None


def build_llm(model: str, small_model: str | None = None, router: str = "rules",
              cache: str | Path | None = None, ledger: Ledger | None = None) -> MeteredLLM:
    def layer(spec: str):
        backend = make_llm(spec)
        return CachedLLM(backend, cache) if cache else backend

    large = layer(model)
    if not small_model:
        return MeteredLLM(large, ledger)
    policy = BanditPolicy(Path(cache).with_name("bandit.json") if cache else ".cache/bandit.json") \
        if router == "bandit" else RulePolicy()
    return MeteredLLM(RoutedLLM(layer(small_model), large, policy), ledger)
