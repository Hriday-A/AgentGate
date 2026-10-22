"""Runs the task suite against an agent configuration and scores every task."""
from __future__ import annotations

import fnmatch
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from opentelemetry import context as otel_context

from ..agent.graph import GraphAgent
from ..agent.react import ReactAgent
from ..agent.toolbox import Toolbox
from ..extraction.extract import extract_invoice
from ..learning.memory import Memory
from ..middleware import Ledger, build_llm
from ..suites import invoice
from ..telemetry import setup as setup_telemetry
from ..telemetry import tracer
from .report import print_run, summarize

AGENTS = {"graph": GraphAgent, "react": ReactAgent}


def _http(method: str, url: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        url, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _healthy(base: str) -> bool:
    try:
        _http("GET", f"{base}/healthz")
        return True
    except OSError:
        return False


@contextmanager
def portal_server(base: str, autostart: bool = True):
    """Use running portals at `base`, or start them for the duration of the run."""
    if _healthy(base):
        yield
        return
    parsed = urlparse(base)
    with socket.socket() as s:
        if s.connect_ex((parsed.hostname, parsed.port)) == 0:
            raise SystemExit(f"Port {parsed.port} is in use by something other than the AgentGate portals.")
    if not autostart:
        raise SystemExit(f"Mock portals are not reachable at {base}. Start them with: python -m agentgate portals")
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentgate.portals", "--host", parsed.hostname, "--port", str(parsed.port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 20
        while not _healthy(base):
            if time.time() > deadline or proc.poll() is not None:
                raise SystemExit("Mock portals failed to start.")
            time.sleep(0.2)
        yield
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def select_tasks(opts) -> list[invoice.Task]:
    tasks = invoice.load_tasks()
    if getattr(opts, "smoke", False):
        tasks = [t for t in tasks if t.smoke]
    if getattr(opts, "difficulty", None):
        tasks = [t for t in tasks if t.difficulty == opts.difficulty]
    if getattr(opts, "tasks", None):
        tasks = [t for t in tasks if fnmatch.fnmatch(t.id, opts.tasks)]
    if getattr(opts, "limit", None):
        tasks = tasks[: opts.limit]
    return tasks


def _create_env(base: str, seed: int, difficulty: str) -> dict:
    """Same task → same environment id → same URLs in every prompt, so repeated runs can hit the cache.
    If that id is taken (the same task running concurrently), fall back to a random one."""
    try:
        return _http("POST", f"{base}/admin/envs", {"seed": seed, "difficulty": difficulty,
                                                   "env_id": f"t{seed}-{difficulty}"})
    except urllib.error.HTTPError as e:
        if e.code != 409:
            raise
        return _http("POST", f"{base}/admin/envs", {"seed": seed, "difficulty": difficulty})


def run_task(task: invoice.Task, opts, run_dir: Path) -> dict:
    from playwright.sync_api import sync_playwright

    base = opts.portal_url
    seed = task.seed + getattr(opts, "seed_offset", 0)  # an offset gives the same task kinds on fresh data
    env = _create_env(base, seed, task.difficulty)
    state_url = f"{base}/admin/envs/{env['env_id']}/state"
    prepared = invoice.prepare(task, _http("GET", state_url))
    ledger = Ledger()  # one ledger per task: agent and extraction calls, every model
    cache = opts.cache_path if getattr(opts, "cache", False) else None
    llm = build_llm(opts.model, getattr(opts, "small_model", None), getattr(opts, "router", "rules"), cache, ledger)
    extract_llm = build_llm(opts.extract_model or opts.model, cache=cache, ledger=ledger)
    agent = AGENTS[opts.agent](llm, max_steps=opts.max_steps)
    hints: list[str] = []
    if getattr(opts, "memory", None):
        lessons = Memory(opts.memory).retrieve(task.kind, task.vendor, task.difficulty, prepared.prompt)
        hints = [l.text for l in lessons]
    task_dir = run_dir / task.id

    t0 = time.perf_counter()
    with tracer.start_as_current_span("eval.task") as span:
        span.set_attributes({"agentgate.task": task.id, "agentgate.difficulty": task.difficulty,
                             "agentgate.kind": task.kind, "agentgate.seed": seed})
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=not opts.headed)
                page = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True).new_page()
                page.goto(env["home"])
                toolbox = Toolbox(page, lambda pdf: extract_invoice(pdf, extract_llm), task_dir)
                result = agent.run(prepared.prompt, toolbox, hints)
                browser.close()
            final_state = _http("GET", state_url)
        finally:
            _http("DELETE", f"{base}/admin/envs/{env['env_id']}")
        elapsed = time.perf_counter() - t0
        passed, detail = prepared.check(final_state, result.answer)
        span.set_attributes({"agentgate.passed": passed, "agentgate.steps": result.steps})

    field_scores = prepared.fields(final_state) if prepared.fields else None
    usage = ledger.summary()
    record = {
        "task_id": task.id,
        "kind": task.kind,
        "vendor": task.vendor,
        "difficulty": task.difficulty,
        "seed": seed,
        "passed": passed,
        "failure": None if passed else (result.stop_reason if result.stop_reason != "done" else "wrong_result"),
        "detail": result.error or detail,
        "answer": result.answer,
        "field_accuracy": round(sum(field_scores.values()) / len(field_scores), 4) if field_scores else None,
        "fields": field_scores,
        "steps": result.steps,
        "failed_actions": result.failed_actions,
        "replans": result.replans,
        "hints": len(hints),
        "seconds": round(elapsed, 2),
        "llm_seconds": usage["llm_seconds"],
        "llm_calls": usage["llm_calls"],
        "cached_calls": usage["cached_calls"],
        "small_calls": usage["small_calls"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "cost_usd": usage["cost_usd"],
    }
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "trace.json").write_text(json.dumps(
        {"task": record, "prompt": prepared.prompt, "hints": hints, "plan": result.plan,
         "models": usage["by_model"], "trace": result.trace}, indent=2, default=str))
    return record


def _harness_error(task: invoice.Task, e: Exception) -> dict:
    return {"task_id": task.id, "kind": task.kind, "vendor": task.vendor, "difficulty": task.difficulty,
            "seed": task.seed, "passed": False, "failure": "harness_error", "detail": f"{type(e).__name__}: {e}",
            "answer": None, "field_accuracy": 0.0 if task.kind.startswith("enter_") else None, "fields": None,
            "steps": 0, "failed_actions": 0, "replans": 0, "hints": 0, "seconds": 0.0, "llm_seconds": 0.0,
            "llm_calls": 0, "cached_calls": 0, "small_calls": 0, "input_tokens": 0, "output_tokens": 0,
            "cost_usd": 0.0}


def run_suite(opts) -> dict:
    """Run the selected tasks and write runs/<run-id>/results.json. Returns the results."""
    setup_telemetry()
    tasks = select_tasks(opts)
    if not tasks:
        raise SystemExit("No tasks matched.")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = Path(opts.out) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    label = opts.label or f"{opts.agent}/{opts.model}"
    print(f"Run {run_id} [{label}]: {len(tasks)} task(s) → {run_dir}")

    with portal_server(opts.portal_url, autostart=not opts.no_autostart), \
            tracer.start_as_current_span("eval.run") as run_span:
        run_span.set_attributes({"agentgate.run_id": run_id, "agentgate.label": label})
        parent = otel_context.get_current()  # worker threads don't inherit it; attach it explicitly

        def one(task: invoice.Task) -> dict:
            token = otel_context.attach(parent)
            try:
                rec = run_task(task, opts, run_dir)
            except Exception as e:  # keep the suite going; record harness errors as failures
                rec = _harness_error(task, e)
            finally:
                otel_context.detach(token)
            print(f"  {'✓' if rec['passed'] else '✗'} {rec['task_id']}", flush=True)
            return rec

        with ThreadPoolExecutor(max_workers=opts.concurrency) as pool:
            records = list(pool.map(one, tasks))

    results = {
        "kind": "agent",
        "run_id": run_id,
        "suite": "invoice",
        "config": {"label": label, "agent": opts.agent, "model": opts.model,
                   "extract_model": opts.extract_model or opts.model, "max_steps": opts.max_steps,
                   "small_model": getattr(opts, "small_model", None),
                   "router": getattr(opts, "router", None) if getattr(opts, "small_model", None) else None,
                   "cache": bool(getattr(opts, "cache", False)), "memory": getattr(opts, "memory", None),
                   "seed_offset": getattr(opts, "seed_offset", 0)},
        "summary": summarize(records),
        "tasks": records,
    }
    out_file = run_dir / "results.json"
    out_file.write_text(json.dumps(results, indent=2))
    results["path"] = str(out_file)
    print_run(results)
    print(f"\nResults: {out_file}")
    return results
