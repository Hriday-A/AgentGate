"""Runs a suite against an agent and scores every task."""
from __future__ import annotations

import fnmatch
import json
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from ..llm import cost_usd, make_llm
from ..agent.loop import Agent
from ..suites import invoice


def _http(method: str, url: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        url, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
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
    if not autostart:
        raise SystemExit(f"Mock portals are not reachable at {base}. Start them with: agentgate portals")
    parsed = urlparse(base)
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentgate.portals", "--host", parsed.hostname, "--port", str(parsed.port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 15
        while not _healthy(base):
            if time.time() > deadline or proc.poll() is not None:
                raise SystemExit("Mock portals failed to start.")
            time.sleep(0.2)
        yield
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def run_task(task: invoice.Task, opts, run_dir: Path) -> dict:
    from playwright.sync_api import sync_playwright

    base = opts.portal_url
    env = _http("POST", f"{base}/admin/envs", {"seed": task.seed})
    state_url = f"{base}/admin/envs/{env['env_id']}/state"
    prepared = invoice.prepare(task, _http("GET", state_url))
    llm = make_llm(opts.model)
    task_dir = run_dir / task.id

    t0 = time.perf_counter()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not opts.headed)
            page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
            page.goto(env["home"])
            result = Agent(llm, max_steps=opts.max_steps).run(prepared.prompt, page, task_dir)
            browser.close()
        final_state = _http("GET", state_url)
    finally:
        _http("DELETE", f"{base}/admin/envs/{env['env_id']}")
    elapsed = time.perf_counter() - t0

    passed, detail = prepared.check(final_state, result.answer)
    if passed:
        failure = None
    elif result.stop_reason != "done":
        failure = result.stop_reason
    else:
        failure = "wrong_result"

    record = {
        "task_id": task.id,
        "kind": task.kind,
        "vendor": task.vendor,
        "seed": task.seed,
        "passed": passed,
        "failure": failure,
        "detail": result.error or detail,
        "answer": result.answer,
        "steps": result.steps,
        "seconds": round(elapsed, 2),
        "llm_seconds": round(result.llm_seconds, 2),
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost_usd": cost_usd(llm.provider, llm.model, result.input_tokens, result.output_tokens),
    }
    (task_dir / "trace.json").write_text(json.dumps(
        {"task": record, "prompt": prepared.prompt, "trace": result.trace}, indent=2, default=str))
    return record


def summarize(records: list[dict]) -> dict:
    n = len(records)
    costs = [r["cost_usd"] for r in records]
    failures: dict[str, int] = {}
    for r in records:
        if r["failure"]:
            failures[r["failure"]] = failures.get(r["failure"], 0) + 1
    return {
        "tasks": n,
        "passed": sum(r["passed"] for r in records),
        "success_rate": round(sum(r["passed"] for r in records) / n, 4) if n else 0.0,
        "avg_steps": round(statistics.mean(r["steps"] for r in records), 2) if n else 0.0,
        "p50_seconds": round(statistics.median(r["seconds"] for r in records), 2) if n else 0.0,
        "input_tokens": sum(r["input_tokens"] for r in records),
        "output_tokens": sum(r["output_tokens"] for r in records),
        "total_cost_usd": None if any(c is None for c in costs) else round(sum(costs), 4),
        "failures": failures,
    }


def print_report(records: list[dict], summary: dict) -> None:
    print(f"\n{'TASK':<30} {'RESULT':<7} {'STEPS':>5} {'TIME':>7} {'COST':>8}  DETAIL")
    for r in records:
        cost = "-" if r["cost_usd"] is None else f"${r['cost_usd']:.4f}"
        result = "PASS" if r["passed"] else "FAIL"
        detail = "" if r["passed"] else f"[{r['failure']}] {r['detail']}"
        print(f"{r['task_id']:<30} {result:<7} {r['steps']:>5} {r['seconds']:>6.1f}s {cost:>8}  {detail[:90]}")
    s = summary
    cost = "n/a" if s["total_cost_usd"] is None else f"${s['total_cost_usd']:.4f}"
    print(f"\nSuccess {s['passed']}/{s['tasks']} ({s['success_rate']:.1%}) · avg steps {s['avg_steps']} · "
          f"p50 {s['p50_seconds']}s · tokens {s['input_tokens']}/{s['output_tokens']} · cost {cost}")
    if s["failures"]:
        print("Failures by type: " + ", ".join(f"{k}={v}" for k, v in sorted(s["failures"].items())))


def _free_port_in_use(base: str) -> None:
    """Fail early with a clear message if a non-AgentGate process holds the port."""
    parsed = urlparse(base)
    with socket.socket() as s:
        if s.connect_ex((parsed.hostname, parsed.port)) == 0 and not _healthy(base):
            raise SystemExit(f"Port {parsed.port} is in use by something other than the AgentGate portals.")


def run_suite(opts) -> int:
    tasks = invoice.load_tasks()
    if opts.tasks:
        tasks = [t for t in tasks if fnmatch.fnmatch(t.id, opts.tasks)]
    if opts.limit:
        tasks = tasks[: opts.limit]
    if not tasks:
        raise SystemExit("No tasks matched.")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = Path(opts.out) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Run {run_id}: {len(tasks)} task(s) with {opts.model} → {run_dir}")

    _free_port_in_use(opts.portal_url)
    with portal_server(opts.portal_url, autostart=not opts.no_autostart):
        def one(task):
            try:
                rec = run_task(task, opts, run_dir)
            except Exception as e:  # keep the suite going; record harness errors as failures
                rec = {"task_id": task.id, "kind": task.kind, "vendor": task.vendor, "seed": task.seed,
                       "passed": False, "failure": "harness_error", "detail": f"{type(e).__name__}: {e}",
                       "answer": None, "steps": 0, "seconds": 0.0, "llm_seconds": 0.0,
                       "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            print(f"  {'✓' if rec['passed'] else '✗'} {rec['task_id']}", flush=True)
            return rec

        with ThreadPoolExecutor(max_workers=opts.concurrency) as pool:
            records = list(pool.map(one, tasks))

    summary = summarize(records)
    results = {"run_id": run_id, "suite": "invoice", "model": opts.model, "summary": summary, "tasks": records}
    out_file = run_dir / "results.json"
    out_file.write_text(json.dumps(results, indent=2))
    print_report(records, summary)
    print(f"\nResults: {out_file}")
    return 0


def compare(baseline_path: str, current_path: str, max_drop_pts: float) -> int:
    """Regression gate: non-zero exit if success rate drops more than `max_drop_pts` points."""
    base = json.loads(Path(baseline_path).read_text())
    cur = json.loads(Path(current_path).read_text())
    b, c = base["summary"]["success_rate"] * 100, cur["summary"]["success_rate"] * 100
    base_pass = {t["task_id"]: t["passed"] for t in base["tasks"]}
    regressed = [t["task_id"] for t in cur["tasks"] if base_pass.get(t["task_id"]) and not t["passed"]]
    fixed = [t["task_id"] for t in cur["tasks"] if base_pass.get(t["task_id"]) is False and t["passed"]]
    print(f"Success rate: {b:.1f}% → {c:.1f}% ({c - b:+.1f} pts)")
    if regressed:
        print("Newly failing: " + ", ".join(regressed))
    if fixed:
        print("Newly passing: " + ", ".join(fixed))
    if b - c > max_drop_pts:
        print(f"GATE FAILED: success dropped more than {max_drop_pts} pts")
        return 1
    print("GATE PASSED")
    return 0
