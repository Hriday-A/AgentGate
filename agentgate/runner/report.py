"""Metrics, the per-run report, the cross-configuration comparison table, and the CI gate."""
from __future__ import annotations

import json
import statistics
from pathlib import Path

DIFFICULTIES = ["easy", "medium", "hard"]


def percentile(q: float, xs: list[float]) -> float:
    if not xs:
        return 0.0
    if len(xs) == 1:
        return xs[0]
    return round(statistics.quantiles(xs, n=100, method="inclusive")[int(q) - 1], 2)


def _rate(xs: list) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def summarize(records: list[dict]) -> dict:
    n = len(records)
    costs = [r["cost_usd"] for r in records]
    seconds = [r["seconds"] for r in records]
    failures: dict[str, int] = {}
    for r in records:
        if r["failure"]:
            failures[r["failure"]] = failures.get(r["failure"], 0) + 1
    field_accs = [r["field_accuracy"] for r in records if r.get("field_accuracy") is not None]
    return {
        "tasks": n,
        "passed": sum(r["passed"] for r in records),
        "success_rate": _rate([r["passed"] for r in records]) or 0.0,
        "by_difficulty": {d: _rate([r["passed"] for r in records if r.get("difficulty") == d])
                          for d in DIFFICULTIES},
        "field_accuracy": round(statistics.mean(field_accs), 4) if field_accs else None,
        "avg_steps": round(statistics.mean(r["steps"] for r in records), 2) if n else 0.0,
        "p50_seconds": percentile(50, seconds),
        "p95_seconds": percentile(95, seconds),
        "input_tokens": sum(r["input_tokens"] for r in records),
        "output_tokens": sum(r["output_tokens"] for r in records),
        "total_cost_usd": None if any(c is None for c in costs) else round(sum(costs), 4),
        "cost_per_task_usd": None if any(c is None for c in costs) or not n else round(sum(costs) / n, 5),
        "failures": failures,
    }


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def print_run(results: dict) -> None:
    records, s = results["tasks"], results["summary"]
    print(f"\n{'TASK':<34} {'RESULT':<6} {'FIELDS':>6} {'STEPS':>5} {'TIME':>7}  DETAIL")
    for r in records:
        detail = "" if r["passed"] else f"[{r['failure']}] {r['detail']}"
        print(f"{r['task_id']:<34} {'PASS' if r['passed'] else 'FAIL':<6} {_pct(r.get('field_accuracy')):>6} "
              f"{r['steps']:>5} {r['seconds']:>6.1f}s  {detail[:80]}")
    cost = "n/a" if s["total_cost_usd"] is None else f"${s['total_cost_usd']:.4f}"
    by_d = " · ".join(f"{d} {_pct(v)}" for d, v in s["by_difficulty"].items() if v is not None)
    print(f"\nSuccess {s['passed']}/{s['tasks']} ({_pct(s['success_rate'])})  [{by_d}]  "
          f"field accuracy {_pct(s['field_accuracy'])}")
    print(f"Steps avg {s['avg_steps']} · latency p50 {s['p50_seconds']}s p95 {s['p95_seconds']}s · "
          f"tokens {s['input_tokens']}/{s['output_tokens']} · cost {cost}")
    if s["failures"]:
        print("Failures: " + ", ".join(f"{k}={v}" for k, v in sorted(s["failures"].items())))


def comparison_table(paths: list[Path]) -> str:
    """One row per results.json, so configurations can be compared side by side (Markdown)."""
    rows = []
    for path in paths:
        r = json.loads(Path(path).read_text())
        if r.get("kind") != "agent":
            continue
        s, c = r["summary"], r["config"]
        cost = "-" if s.get("cost_per_task_usd") is None else f"${s['cost_per_task_usd']:.4f}"
        rows.append(
            f"| {c['label']} | {s['tasks']} | **{_pct(s['success_rate'])}** | "
            + " | ".join(_pct(s["by_difficulty"].get(d)) for d in DIFFICULTIES)
            + f" | {_pct(s.get('field_accuracy'))} | {s['avg_steps']} | {s['p50_seconds']}s | {s['p95_seconds']}s"
              f" | {(s['input_tokens'] + s['output_tokens']) // max(s['tasks'], 1):,} | {cost} |")
    header = ("| Config | Tasks | Success | Easy | Medium | Hard | Field acc. | Steps | p50 | p95 | Tokens/task "
              "| Cost/task |\n|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
    return header + "\n" + "\n".join(rows)


def compare(baseline_path: str, current_path: str, max_drop_pts: float) -> int:
    """Regression gate: non-zero exit if success rate drops more than `max_drop_pts` points.

    Only tasks present in both runs are compared, so a smoke run can be gated
    against a full baseline.
    """
    base = json.loads(Path(baseline_path).read_text())
    cur = json.loads(Path(current_path).read_text())
    base_by_id = {t["task_id"]: t for t in base["tasks"]}
    shared = [t for t in cur["tasks"] if t["task_id"] in base_by_id]
    if not shared:
        print("GATE FAILED: no tasks in common with the baseline")
        return 1
    b = 100 * sum(base_by_id[t["task_id"]]["passed"] for t in shared) / len(shared)
    c = 100 * sum(t["passed"] for t in shared) / len(shared)
    regressed = [t["task_id"] for t in shared if base_by_id[t["task_id"]]["passed"] and not t["passed"]]
    fixed = [t["task_id"] for t in shared if not base_by_id[t["task_id"]]["passed"] and t["passed"]]
    print(f"Success rate on {len(shared)} shared tasks: {b:.1f}% → {c:.1f}% ({c - b:+.1f} pts)")
    if regressed:
        print("Newly failing: " + ", ".join(regressed))
    if fixed:
        print("Newly passing: " + ", ".join(fixed))
    if b - c > max_drop_pts:
        print(f"GATE FAILED: success dropped more than {max_drop_pts} pts")
        return 1
    print("GATE PASSED")
    return 0
