"""Send a finished run (results.json + traces + screenshots) to the AgentGate API.

Authentication is the agent's API key (X-Api-Key), created in the dashboard.
Ingest is idempotent on the run id, so publishing the same run twice is safe.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import httpx

INGEST = "mutation($input: RunInput!) { ingestRun(input: $input) { id } }"


def _git(*args: str) -> str | None:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def _git_info() -> tuple[str | None, str | None]:
    # In GitHub Actions, prefer the PR's branch and commit over the merge commit's.
    sha = os.environ.get("GITHUB_SHA") or _git("rev-parse", "HEAD")
    branch = os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME") or _git("rev-parse", "--abbrev-ref", "HEAD")
    return sha, branch


def _step_input(step: dict) -> dict:
    return {
        "stepNo": step["step"],
        "action": step.get("action"),
        "args": json.dumps(step.get("args")) if step.get("args") is not None else None,
        "thought": (step.get("thought") or "")[:4000] or None,
        "outcome": step.get("outcome"),
        "ok": step.get("ok"),
        "verified": step.get("verified"),
        "feedback": step.get("feedback"),
        "url": step.get("url"),
        "screenshot": step.get("screenshot"),
    }


def build_input(run_dir: Path) -> dict:
    results = json.loads((run_dir / "results.json").read_text())
    s, config = results["summary"], results["config"]
    sha, branch = _git_info()
    tasks = []
    for r in results["tasks"]:
        trace_file = run_dir / r["task_id"] / "trace.json"
        trace = json.loads(trace_file.read_text())["trace"] if trace_file.exists() else []
        tasks.append({
            "taskId": r["task_id"], "kind": r["kind"], "vendor": r["vendor"], "difficulty": r["difficulty"],
            "passed": r["passed"], "failure": r["failure"], "detail": r["detail"], "answer": r["answer"],
            "fieldAccuracy": r["field_accuracy"], "steps": r["steps"], "replans": r.get("replans", 0),
            "seconds": r["seconds"], "inputTokens": r["input_tokens"], "outputTokens": r["output_tokens"],
            "costUsd": r["cost_usd"],
            "trace": [_step_input(st) for st in trace if "step" in st],
        })
    return {
        "externalId": results["run_id"], "label": config["label"], "agentKind": config["agent"],
        "model": config["model"], "extractModel": config.get("extract_model"), "gitSha": sha, "gitBranch": branch,
        "summary": {
            "tasks": s["tasks"], "passed": s["passed"], "successRate": s["success_rate"],
            "fieldAccuracy": s["field_accuracy"], "avgSteps": s["avg_steps"], "p50Seconds": s["p50_seconds"],
            "p95Seconds": s["p95_seconds"], "inputTokens": s["input_tokens"], "outputTokens": s["output_tokens"],
            "totalCostUsd": s["total_cost_usd"],
        },
        "results": tasks,
    }


def publish(run_dir: Path, api_url: str, api_key: str, client: httpx.Client | None = None) -> str:
    """Returns the run's id in the API."""
    client = client or httpx.Client(timeout=60)
    headers = {"X-Api-Key": api_key}
    payload = build_input(run_dir)
    resp = client.post(f"{api_url.rstrip('/')}/graphql", headers=headers,
                       json={"query": INGEST, "variables": {"input": payload}})
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"Ingest failed: {body['errors'][0]['message']}")
    run_id = body["data"]["ingestRun"]["id"]

    uploaded = 0
    for task in payload["results"]:
        for step in task["trace"]:
            name = step["screenshot"]
            path = run_dir / task["taskId"] / name if name else None
            if path and path.exists():
                url = f"{api_url.rstrip('/')}/api/artifacts/runs/{payload['externalId']}/{task['taskId']}/{name}"
                client.put(url, headers={**headers, "Content-Type": "image/jpeg"},
                           content=path.read_bytes()).raise_for_status()
                uploaded += 1
    print(f"Published run {payload['externalId']} → API run {run_id} ({uploaded} screenshots)")
    return run_id
