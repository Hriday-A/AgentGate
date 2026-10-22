"""Runs the task suite against an agent configuration and scores every task."""
from __future__ import annotations

import fnmatch
import json
import socket
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from ..agent.graph import GraphAgent
from ..agent.react import ReactAgent
from ..agent.toolbox import Toolbox
from ..extraction.extract import extract_invoice
from ..llm import cost_usd, make_llm
from ..suites import invoice
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


def run_task(task: invoice.Task, opts, run_dir: Path) -> dict:
    from playwright.sync_api import sync_playwright

    base = opts.portal_url
    env = _http("POST", f"{base}/admin/envs", {"seed": task.seed, "difficulty": task.difficulty})
    state_url = f"{base}/admin/envs/{env['env_id']}/state"
    prepared = invoice.prepare(task, _http("GET", state_url))
    llm = make_llm(opts.model)
    extract_llm = make_llm(opts.extract_model) if opts.extract_model else llm
    agent = AGENTS[opts.agent](llm, max_steps=opts.max_steps)
    task_dir = run_dir / task.id

    t0 = time.perf_counter()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not opts.headed)
            page = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True).new_page()
            page.goto(env["home"])
            toolbox = Toolbox(page, lambda pdf: extract_invoice(pdf, extract_llm), task_dir)
            result = agent.run(prepared.prompt, toolbox)
            browser.close()
        final_state = _http("GET", state_url)
    finally:
        _http("DELETE", f"{base}/admin/envs/{env['env_id']}")
    elapsed = time.perf_counter() - t0

    passed, detail = prepared.check(final_state, result.answer)
    field_scores = prepared.fields(final_state) if prepared.fields else None
    ex_in, ex_out = toolbox.extraction_tokens
    tokens_in, tokens_out = result.input_tokens + ex_in, result.output_tokens + ex_out
    record = {
        "task_id": task.id,
        "kind": task.kind,
        "vendor": task.vendor,
        "difficulty": task.difficulty,
        "seed": task.seed,
        "passed": passed,
        "failure": None if passed else (result.stop_reason if result.stop_reason != "done" else "wrong_result"),
        "detail": result.error or detail,
        "answer": result.answer,
        "field_accuracy": round(sum(field_scores.values()) / len(field_scores), 4) if field_scores else None,
        "fields": field_scores,
        "steps": result.steps,
        "failed_actions": result.failed_actions,
        "replans": result.replans,
        "seconds": round(elapsed, 2),
        "llm_seconds": round(result.llm_seconds, 2),
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cost_usd": cost_usd(llm.provider, llm.model, tokens_in, tokens_out),
    }
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "trace.json").write_text(json.dumps(
        {"task": record, "prompt": prepared.prompt, "plan": result.plan, "trace": result.trace},
        indent=2, default=str))
    return record


def _harness_error(task: invoice.Task, e: Exception) -> dict:
    return {"task_id": task.id, "kind": task.kind, "vendor": task.vendor, "difficulty": task.difficulty,
            "seed": task.seed, "passed": False, "failure": "harness_error", "detail": f"{type(e).__name__}: {e}",
            "answer": None, "field_accuracy": 0.0 if task.kind.startswith("enter_") else None, "fields": None,
            "steps": 0, "failed_actions": 0, "replans": 0, "seconds": 0.0, "llm_seconds": 0.0,
            "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


def run_suite(opts) -> int:
    tasks = select_tasks(opts)
    if not tasks:
        raise SystemExit("No tasks matched.")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = Path(opts.out) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    label = opts.label or f"{opts.agent}/{opts.model}"
    print(f"Run {run_id} [{label}]: {len(tasks)} task(s) → {run_dir}")

    with portal_server(opts.portal_url, autostart=not opts.no_autostart):
        def one(task: invoice.Task) -> dict:
            try:
                rec = run_task(task, opts, run_dir)
            except Exception as e:  # keep the suite going; record harness errors as failures
                rec = _harness_error(task, e)
            print(f"  {'✓' if rec['passed'] else '✗'} {rec['task_id']}", flush=True)
            return rec

        with ThreadPoolExecutor(max_workers=opts.concurrency) as pool:
            records = list(pool.map(one, tasks))

    results = {
        "kind": "agent",
        "run_id": run_id,
        "suite": "invoice",
        "config": {"label": label, "agent": opts.agent, "model": opts.model,
                   "extract_model": opts.extract_model or opts.model, "max_steps": opts.max_steps},
        "summary": summarize(records),
        "tasks": records,
    }
    out_file = run_dir / "results.json"
    out_file.write_text(json.dumps(results, indent=2))
    print_run(results)
    print(f"\nResults: {out_file}")
    return 0
