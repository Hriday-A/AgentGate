import json
import time

import httpx
import pytest

from agentgate.llm import OpenAICompatLLM, RateLimiter, make_llm, parse_json_object


@pytest.mark.parametrize("text, expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Here you go: {"a": {"b": 2}} hope that helps', {"a": {"b": 2}}),
    ("no json here", None),
    ("[1, 2]", None),
])
def test_parse_json_object(text, expected):
    assert parse_json_object(text) == expected


def test_rate_limiter_spaces_calls():
    limiter = RateLimiter(rpm=600)  # one call per 0.1 s
    start = time.monotonic()
    for _ in range(4):
        limiter.wait()
    assert time.monotonic() - start >= 0.29


def test_make_llm_validates_spec(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError):
        make_llm("gemini")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        make_llm("gemini:gemini-3.5-flash")


def _llm_with(handler) -> OpenAICompatLLM:
    llm = OpenAICompatLLM("ollama", "test-model", max_retries=2)
    llm.client = httpx.Client(base_url="http://fake", transport=httpx.MockTransport(handler))
    return llm


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                     "usage": {"prompt_tokens": 7, "completion_tokens": 3}})


def test_extract_falls_back_to_json_mode_when_schema_mode_is_rejected():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body["response_format"]["type"])
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": "response_format not supported"})
        return _reply('{"ok": true}')

    llm = _llm_with(handler)
    resp = llm.extract("sys", "prompt", [b"\xff\xd8\xff fake jpeg"], {"type": "object"}, "x")
    assert resp.data == {"ok": True} and (resp.input_tokens, resp.output_tokens) == (7, 3)
    assert seen == ["json_schema", "json_object"]
    llm.extract("sys", "prompt", [], {"type": "object"}, "x")
    assert seen[-1] == "json_object"  # remembered, no second 400


def test_retries_on_429(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    statuses = iter([429, 503, 200])

    def handler(request):
        status = next(statuses)
        return _reply('{"n": 1}') if status == 200 else httpx.Response(status, headers={"retry-after": "1"})

    assert _llm_with(handler).extract("s", "p", [], {}, "x").data == {"n": 1}


def test_retries_after_a_timeout(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ReadTimeout("hung", request=request)
        return _reply('{"n": 1}')

    assert _llm_with(handler).extract("s", "p", [], {}, "x").data == {"n": 1} and len(attempts) == 2


def test_tool_calls_are_parsed():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "click", "arguments": '{"element": 3}'}}]}}]})

    resp = _llm_with(handler).complete("s", [{"role": "user", "text": "hi"}],
                                       [{"name": "click", "description": "d", "input_schema": {}}])
    assert resp.tool_calls[0].name == "click" and resp.tool_calls[0].args == {"element": 3}


def test_thought_signatures_are_sent_back_with_the_call():
    sig = {"google": {"thought_signature": "abc"}}
    sent = []

    def handler(request):
        sent.append(json.loads(request.content)["messages"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "type": "function", "extra_content": sig,
             "function": {"name": "click", "arguments": '{"element": 3}'}}]}}]})

    llm = _llm_with(handler)
    first = llm.complete("s", [{"role": "user", "text": "hi"}], [])
    assert first.tool_calls[0].extra == sig
    llm.complete("s", [{"role": "user", "text": "hi"},
                       {"role": "assistant", "text": "", "tool_calls": first.tool_calls},
                       {"role": "tool", "results": [{"id": "c1", "content": "ok"}]}], [])
    assert sent[1][2]["tool_calls"][0]["extra_content"] == sig


def test_non_object_tool_arguments_become_empty():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "click", "arguments": '"element 3"'}}]}}]})

    resp = _llm_with(handler).complete("s", [{"role": "user", "text": "hi"}], [])
    assert resp.tool_calls[0].args == {}


def test_daily_quota_fails_fast_instead_of_retrying(monkeypatch):
    from agentgate.llm import QuotaExhausted
    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, json={"error": {"code": 429, "message": "Quota exceeded: limit 20 per day",
                                                   "details": [{"retryDelay": "36207s"}]}})

    with pytest.raises(QuotaExhausted, match="resets in ~10.1 h"):
        _llm_with(handler).extract("s", "p", [], {}, "x")
    assert len(calls) == 1 and slept == []


def test_short_server_delay_is_honored(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    responses = iter([httpx.Response(429, json={"error": {"details": [{"retryDelay": "7s"}]}}), _reply('{"a": 1}')])
    assert _llm_with(lambda r: next(responses)).extract("s", "p", [], {}, "x").data == {"a": 1}
    assert slept == [7.0]
