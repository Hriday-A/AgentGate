"""Metering, caching and routing layers, with fake backends (no network)."""
import pytest

from agentgate.llm import JSONResponse, LLMResponse, ToolCall
from agentgate.middleware import BanditPolicy, CachedLLM, Ledger, MeteredLLM, RoutedLLM, RulePolicy, classify


class Fake:
    def __init__(self, name, tool_calls=True):
        self.provider, self.model = name.split(":")
        self.name, self.tool_calls, self.calls = name, tool_calls, 0

    def complete(self, system, history, tools):
        self.calls += 1
        calls = [ToolCall("1", "click", {"element": 1})] if self.tool_calls else []
        return LLMResponse("", calls, 100, 10, model=self.name)

    def extract(self, system, prompt, images, schema, name):
        self.calls += 1
        return JSONResponse({"ok": True}, "{}", 200, 20, model=self.name)


def graph_prompt(step: str, failed: bool = False) -> list[dict]:
    feedback = "\n\n⚠ Your last action failed: covered" if failed else ""
    return [{"role": "user", "text": f"Task: x\n\nPlan:\n  ✓ 1. a\n  → 2. {step}   (current){feedback}\n\nCurrent page:\n..."}]


@pytest.mark.parametrize("step, expected", [
    ("Sign in to the Acme portal", "login"),
    ("Dismiss the cookie banner", "popup"),
    ("Download the invoice PDF", "download"),
    ("Extract the invoice fields", "extract"),
    ("Fill and submit the AP form", "form"),
    ("Open invoice ACM-12345", "navigate"),
    ("Report the total amount", "answer"),
])
def test_classify_plan_steps(step, expected):
    assert classify(graph_prompt(step)) == (expected, False)


def test_classify_detects_failure():
    assert classify(graph_prompt("Sign in", failed=True)) == ("login", True)


def test_rules_send_easy_steps_small_and_everything_else_large():
    small, large = Fake("ollama:small"), Fake("gemini:large")
    llm = RoutedLLM(small, large, RulePolicy())
    assert llm.complete("s", graph_prompt("Sign in to the portal"), []).model == "ollama:small"
    assert llm.complete("s", graph_prompt("Sign in to the portal", failed=True), []).model == "gemini:large"
    assert llm.complete("s", graph_prompt("Fill the AP form"), []).model == "gemini:large"
    assert llm.extract("s", "p", [], {}, "plan").model == "gemini:large"  # planning/extraction never go small


def test_small_model_without_a_tool_call_escalates():
    small, large = Fake("ollama:small", tool_calls=False), Fake("gemini:large")
    resp = RoutedLLM(small, large, RulePolicy()).complete("s", graph_prompt("Sign in"), [])
    assert resp.model == "gemini:large" and small.calls == 1 and large.calls == 1


def test_small_model_error_escalates_instead_of_failing_the_task():
    class Hangs(Fake):
        def complete(self, *a):
            self.calls += 1
            raise TimeoutError("local model took too long")

    small, large = Hangs("ollama:small"), Fake("gemini:large")
    resp = RoutedLLM(small, large, RulePolicy()).complete("s", graph_prompt("Sign in"), [])
    assert resp.model == "gemini:large" and small.calls == 1


def test_small_model_is_built_to_fail_fast(monkeypatch):
    from agentgate import middleware
    built = {}

    def fake_make_llm(spec, **options):
        built[spec] = options
        return Fake(spec)

    monkeypatch.setattr(middleware, "make_llm", fake_make_llm)
    middleware.build_llm("gemini:large", small_model="ollama:small")
    assert built["ollama:small"]["max_retries"] == 0 and built["gemini:large"] == {}


def test_bandit_learns_which_arm_works(tmp_path):
    policy = BanditPolicy(tmp_path / "bandit.json", epsilon=0.0)
    for _ in range(5):
        policy.update("login:ok", "small", 0.0)  # small keeps failing on login
        policy.update("login:ok", "large", 0.9)
    assert policy.choose("login", False) == "large"
    assert BanditPolicy(tmp_path / "bandit.json", epsilon=0.0).choose("login", False) == "large"  # persisted


def test_routed_feedback_reaches_the_policy(tmp_path):
    policy = BanditPolicy(tmp_path / "b.json", epsilon=0.0)
    llm = RoutedLLM(Fake("ollama:small"), Fake("gemini:large"), policy)
    llm.complete("s", graph_prompt("Sign in"), [])
    llm.feedback(True)
    assert sum(n for n, _ in policy.stats["login:ok"].values()) == 1


def test_cache_hits_skip_the_backend_and_cost_nothing(tmp_path):
    backend = Fake("gemini:large")
    ledger = Ledger()
    llm = MeteredLLM(CachedLLM(backend, tmp_path / "c.sqlite"), ledger)
    history = graph_prompt("Sign in")
    first, second = llm.complete("s", history, []), llm.complete("s", history, [])
    assert backend.calls == 1 and not first.cached and second.cached
    assert second.tool_calls == first.tool_calls
    llm.extract("s", "p", [b"img"], {}, "x")
    llm.extract("s", "p", [b"img"], {}, "x")
    llm.extract("s", "p", [b"other image"], {}, "x")  # different bytes, different key
    assert backend.calls == 3
    s = ledger.summary()
    assert (s["llm_calls"], s["cached_calls"], s["input_tokens"]) == (5, 2, 500)


def test_cache_does_not_store_non_answers(tmp_path):
    backend = Fake("gemini:large", tool_calls=False)
    llm = CachedLLM(backend, tmp_path / "c.sqlite")
    llm.complete("s", graph_prompt("x"), [])
    llm.complete("s", graph_prompt("x"), [])
    assert backend.calls == 2


def test_ledger_splits_models_when_routed():
    ledger = Ledger()
    llm = MeteredLLM(RoutedLLM(Fake("ollama:small"), Fake("gemini:large"), RulePolicy()), ledger)
    llm.complete("s", graph_prompt("Sign in"), [])
    llm.complete("s", graph_prompt("Fill the AP form"), [])
    s = ledger.summary()
    assert s["small_calls"] == 1 and s["by_model"] == {"gemini:large": 1, "ollama:small": 1}
    assert s["cost_usd"] == 0.0  # both are free providers
