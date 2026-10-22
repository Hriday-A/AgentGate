import json

import httpx

from agentgate.runner.publish import build_input, publish


def make_run(tmp_path):
    run = tmp_path / "20261022-120000"
    (run / "easy-acme-amount").mkdir(parents=True)
    task = {"task_id": "easy-acme-amount", "kind": "lookup_amount", "vendor": "acme", "difficulty": "easy",
            "passed": True, "failure": None, "detail": "ok", "answer": "12.00", "field_accuracy": None,
            "steps": 2, "replans": 0, "seconds": 3.5, "input_tokens": 10, "output_tokens": 2, "cost_usd": 0.0}
    (run / "results.json").write_text(json.dumps({
        "run_id": "20261022-120000", "config": {"label": "graph/x", "agent": "graph", "model": "m", "extract_model": "m"},
        "summary": {"tasks": 1, "passed": 1, "success_rate": 1.0, "field_accuracy": None, "avg_steps": 2,
                    "p50_seconds": 3.5, "p95_seconds": 3.5, "input_tokens": 10, "output_tokens": 2,
                    "total_cost_usd": 0.0},
        "tasks": [task]}))
    (run / "easy-acme-amount" / "trace.json").write_text(json.dumps({"trace": [
        {"event": "plan", "plan": ["a"]},  # plan events are not steps
        {"step": 1, "action": "click", "args": {"element": 3}, "ok": True, "verified": True, "screenshot": "step_001.jpg"},
        {"step": 2, "action": "done", "args": {"answer": "12.00"}},
    ]}))
    (run / "easy-acme-amount" / "step_001.jpg").write_bytes(b"\xff\xd8\xff jpeg")
    return run


def test_build_input_maps_results_and_traces(tmp_path):
    payload = build_input(make_run(tmp_path))
    assert payload["externalId"] == "20261022-120000" and payload["summary"]["successRate"] == 1.0
    steps = payload["results"][0]["trace"]
    assert [s["stepNo"] for s in steps] == [1, 2]
    assert steps[0]["args"] == '{"element": 3}' and steps[0]["screenshot"] == "step_001.jpg"


def test_publish_ingests_then_uploads_screenshots(tmp_path):
    calls = []

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path, request.headers.get("x-api-key")))
        if request.url.path == "/graphql":
            return httpx.Response(200, json={"data": {"ingestRun": {"id": "7"}}})
        return httpx.Response(201)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert publish(make_run(tmp_path), "http://api", "ag_test", client) == "7"
    assert calls == [("POST", "/graphql", "ag_test"),
                     ("PUT", "/api/artifacts/runs/20261022-120000/easy-acme-amount/step_001.jpg", "ag_test")]
