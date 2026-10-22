import json

from agentgate.runner.report import compare, comparison_table, percentile, summarize


def rec(task_id, passed, difficulty="easy", seconds=10.0, field_accuracy=None):
    return {"task_id": task_id, "passed": passed, "difficulty": difficulty, "failure": None if passed else "wrong_result",
            "field_accuracy": field_accuracy, "steps": 5, "seconds": seconds, "input_tokens": 100,
            "output_tokens": 10, "cost_usd": 0.0}


def results(records, label="cfg"):
    return {"kind": "agent", "config": {"label": label}, "summary": summarize(records), "tasks": records}


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return str(path)


def test_summary_metrics():
    s = summarize([rec("a", True, "easy", 10, 1.0), rec("b", False, "hard", 30, 0.5), rec("c", True, "hard", 20)])
    assert s["success_rate"] == round(2 / 3, 4)
    assert s["by_difficulty"] == {"easy": 1.0, "medium": None, "hard": 0.5}
    assert s["field_accuracy"] == 0.75  # only tasks that have field scores count
    assert s["p50_seconds"] == 20.0
    assert s["failures"] == {"wrong_result": 1}


def test_percentile_edges():
    assert percentile(95, []) == 0.0
    assert percentile(95, [4.0]) == 4.0
    assert percentile(95, [float(i) for i in range(1, 101)]) == 95.05


def test_gate_passes_and_fails(tmp_path):
    base = write(tmp_path, "base.json", results([rec(t, True) for t in "abcd"]))
    same = write(tmp_path, "same.json", results([rec(t, True) for t in "abcd"]))
    worse = write(tmp_path, "worse.json", results([rec("a", False), rec("b", False), rec("c", True), rec("d", True)]))
    assert compare(base, same, max_drop_pts=5) == 0
    assert compare(base, worse, max_drop_pts=25) == 1  # 50-point drop


def test_gate_compares_only_shared_tasks(tmp_path):
    # A full baseline with some failures elsewhere vs a smoke run of two tasks that both still pass.
    base = write(tmp_path, "base.json", results([rec("smoke1", True), rec("smoke2", True), rec("other", False)]))
    smoke = write(tmp_path, "smoke.json", results([rec("smoke1", True), rec("smoke2", True)]))
    assert compare(base, smoke, max_drop_pts=0) == 0
    unrelated = write(tmp_path, "x.json", results([rec("zzz", True)]))
    assert compare(base, unrelated, max_drop_pts=100) == 1


def test_comparison_table(tmp_path):
    a = write(tmp_path, "a.json", results([rec("x", True)], "graph/gemini"))
    b = write(tmp_path, "b.json", results([rec("x", False)], "react/gemini"))
    table = comparison_table([a, b])
    assert table.count("\n") == 3  # header, separator, two rows
    assert "| graph/gemini | 1 | **100%** |" in table and "| react/gemini | 1 | **0%** |" in table
