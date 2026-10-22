"""Learning rounds: run → reflect on failures → add lessons → run again on fresh data.

Round r uses seed offset r × 1000, so every round sees new invoices and new layouts. Improvement
therefore has to come from general lessons about the portals, not from remembering round 1's answers.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from ..middleware import build_llm
from ..portals.data import VENDORS
from ..runner.report import _pct
from ..runner.run import run_suite
from ..suites import invoice
from .memory import Memory
from .reflect import demonstration, reflect


def learn(opts) -> dict:
    memory = Memory(opts.memory)
    reflector = build_llm(opts.reflect_model or opts.model)
    tasks = {t.id: t for t in invoice.load_tasks()}
    rounds = []

    for r in range(1, opts.rounds + 1):
        round_opts = copy.copy(opts)
        round_opts.seed_offset = r * 1000
        round_opts.label = f"{opts.label or 'learning'} · round {r}"
        print(f"\n=== Round {r}: {len(memory.lessons)} lessons in memory ===")
        results = run_suite(round_opts)
        run_dir = Path(results["path"]).parent

        new_lessons = []
        for rec in results["tasks"]:
            task = tasks[rec["task_id"]]
            trace_file = run_dir / task.id / "trace.json"
            if not trace_file.exists():
                continue
            saved = json.loads(trace_file.read_text())
            vendor_name = VENDORS[task.vendor]["name"]
            if rec["passed"]:
                demo = demonstration(task, vendor_name, saved["trace"], r)
                new_lessons += [demo] if demo else []
            elif rec["failure"] not in ("harness_error", "llm_error"):  # infrastructure failures teach nothing
                try:
                    new_lessons += reflect(reflector, task, vendor_name, saved["prompt"], rec["failure"],
                                           saved["trace"], r)
                except Exception as e:
                    print(f"  reflection failed for {task.id}: {type(e).__name__}: {e}")
        added = memory.add(new_lessons)
        s = results["summary"]
        rounds.append({"round": r, "success_rate": s["success_rate"], "field_accuracy": s["field_accuracy"],
                       "avg_steps": s["avg_steps"], "p95_seconds": s["p95_seconds"],
                       "lessons_before": len(memory.lessons) - added, "lessons_added": added,
                       "results": results["path"]})

    print("\nRound  Success  Field acc.  Steps  p95     Lessons used → added")
    for x in rounds:
        print(f"{x['round']:>5}  {_pct(x['success_rate']):>7}  {_pct(x['field_accuracy']):>10}  "
              f"{x['avg_steps']:>5}  {x['p95_seconds']:>5}s  {x['lessons_before']:>4} → +{x['lessons_added']}")
    out = Path(opts.out) / "learning" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") / "rounds.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"memory": str(opts.memory), "rounds": rounds}, indent=2))
    print(f"\nRounds summary: {out}")
    return {"rounds": rounds, "path": str(out)}
