"""Model backends shared by the agent and the extractor.

A model is named "provider:model", e.g.
  gemini:gemini-3.5-flash        (free tier via Google AI Studio)
  ollama:llama3.2                (local)
  groq:… / openrouter:… / hf:…   (any OpenAI-compatible endpoint)

Every provider speaks the OpenAI chat-completions format, so one client
(OpenAICompatLLM) covers them all; only the base URL and key differ.

Two operations:
  complete(system, history, tools)          → tool calls for the agent loop
  extract(system, prompt, images, schema)   → one JSON object matching a schema

History format (provider-neutral):
  {"role": "user", "text": str, "observation"?: str}
  {"role": "assistant", "text": str, "tool_calls": [ToolCall, ...]}
  {"role": "tool", "results": [{"id": str, "content": str}], "observation"?: str}
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field

import httpx

# USD per million tokens (input, output). Free tiers and local models cost 0.
# Verify against current provider pricing before quoting numbers.
PRICES: dict[str, tuple[float, float]] = {}
FREE_PROVIDERS = {"ollama", "gemini"}

# provider -> (base URL, env var holding the API key, default requests per minute)
OPENAI_COMPAT = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", 10),
    "ollama": (os.environ.get("OLLAMA_HOST", "http://localhost:11434") + "/v1", None, 0),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", 30),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", 20),
    "hf": ("https://router.huggingface.co/v1", "HF_TOKEN", 0),
}


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""        # "provider:model" that actually answered (matters once calls are routed)
    cached: bool = False


@dataclass
class JSONResponse:
    data: dict | None        # parsed JSON object, or None if the reply wasn't valid JSON
    text: str                # raw reply, kept for debugging
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    cached: bool = False


class RateLimiter:
    """Spaces calls to at most `rpm` per minute (0 = unlimited). Shared across threads."""

    def __init__(self, rpm: int):
        self.interval = 60.0 / rpm if rpm else 0.0
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
        time.sleep(start - now)


_LIMITERS: dict[str, RateLimiter] = {}


def _limiter(provider: str, default_rpm: int) -> RateLimiter:
    if provider not in _LIMITERS:
        rpm = int(os.environ.get(f"AGENTGATE_{provider.upper()}_RPM", default_rpm))
        _LIMITERS[provider] = RateLimiter(rpm)
    return _LIMITERS[provider]


def parse_json_object(text: str) -> dict | None:
    """Pull the first JSON object out of a reply, tolerating ```json fences and chatter."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = text.find("{")
    if start == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _content(msg: dict) -> str:
    obs = msg.get("observation")
    return f"{msg.get('text', '')}\n\n{obs}".strip() if obs else msg.get("text", "")


def _data_url(image: bytes) -> str:
    kind = "image/jpeg" if image[:3] == b"\xff\xd8\xff" else "image/png"
    return f"data:{kind};base64,{base64.b64encode(image).decode()}"


class OpenAICompatLLM:
    """Any endpoint that implements POST /chat/completions in OpenAI's format."""

    def __init__(self, provider: str, model: str, timeout: float | None = None, max_retries: int = 5):
        base_url, key_env, rpm = OPENAI_COMPAT[provider]
        self.spec = f"{provider}:{model}"
        # Local models on a laptop can take minutes per call; hosted ones shouldn't.
        timeout = timeout or float(os.environ.get("AGENTGATE_LLM_TIMEOUT", 600 if provider == "ollama" else 120))
        self.provider, self.model = provider, model
        key = os.environ.get(key_env, "") if key_env else "local"
        if key_env and not key:
            raise RuntimeError(f"Set {key_env} to use {provider} models.")
        self.client = httpx.Client(base_url=base_url, timeout=timeout,
                                   headers={"Authorization": f"Bearer {key}"})
        self.limiter = _limiter(provider, rpm)
        self.max_retries = max_retries
        self.json_schema_supported = True  # flipped off if the endpoint rejects response_format=json_schema

    def _post(self, body: dict) -> dict:
        """POST with rate limiting, and exponential backoff on 429/5xx."""
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            resp = self.client.post("/chat/completions", json=body)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self.max_retries:
                    resp.raise_for_status()
                retry_after = resp.headers.get("retry-after", "")
                time.sleep(float(retry_after) if retry_after.replace(".", "").isdigit() else 2 ** attempt * 2)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("unreachable")

    @staticmethod
    def _usage(payload: dict) -> tuple[int, int]:
        usage = payload.get("usage") or {}
        return usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)

    def _messages(self, system: str, history: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for msg in history:
            if msg["role"] == "user":
                out.append({"role": "user", "content": _content(msg)})
            elif msg["role"] == "assistant":
                out.append({
                    "role": "assistant",
                    "content": msg.get("text", ""),
                    "tool_calls": [
                        {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.args)}}
                        for c in msg["tool_calls"]
                    ],
                })
            else:
                out += [{"role": "tool", "tool_call_id": r["id"], "content": r["content"]} for r in msg["results"]]
                if msg.get("observation"):
                    out.append({"role": "user", "content": msg["observation"]})
        return out

    def complete(self, system: str, history: list[dict], tools: list[dict]) -> LLMResponse:
        payload = self._post({
            "model": self.model,
            "temperature": 0,  # reproducible runs, and identical requests can be served from cache
            "messages": self._messages(system, history),
            "tools": [{"type": "function", "function": {
                "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools],
        })
        message = payload["choices"][0]["message"]
        calls = []
        for i, c in enumerate(message.get("tool_calls") or []):
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):  # some models send a bare string or list; tools need an object
                args = {}
            calls.append(ToolCall(c.get("id") or f"call_{i}", c["function"]["name"], args))
        return LLMResponse(message.get("content") or "", calls, *self._usage(payload), model=self.spec)

    def extract(self, system: str, prompt: str, images: list[bytes], schema: dict, name: str) -> JSONResponse:
        content: list[dict] = [{"type": "text", "text": prompt}]
        content += [{"type": "image_url", "image_url": {"url": _data_url(img)}} for img in images]

        def body(schema_mode: bool) -> dict:
            if schema_mode:  # the endpoint constrains decoding to the schema
                fmt = {"type": "json_schema", "json_schema": {"name": name, "schema": schema}}
                sys_text = system
            else:  # plain JSON mode: the schema has to travel in the prompt instead
                fmt = {"type": "json_object"}
                sys_text = f"{system}\n\nReply with one JSON object matching this schema:\n{json.dumps(schema)}"
            return {"model": self.model, "temperature": 0, "response_format": fmt,
                    "messages": [{"role": "system", "content": sys_text}, {"role": "user", "content": content}]}

        try:
            payload = self._post(body(self.json_schema_supported))
        except httpx.HTTPStatusError as e:
            # Some endpoints reject schema-constrained output; remember that and fall back to JSON mode.
            if e.response.status_code != 400 or not self.json_schema_supported:
                raise
            self.json_schema_supported = False
            payload = self._post(body(False))
        text = payload["choices"][0]["message"].get("content") or ""
        return JSONResponse(parse_json_object(text), text, *self._usage(payload), model=self.spec)


def make_llm(spec: str):
    """Build a backend from 'provider:model'."""
    provider, _, model = spec.partition(":")
    if not model:
        raise ValueError(f"Model spec {spec!r} must look like provider:model")
    if provider in OPENAI_COMPAT:
        return OpenAICompatLLM(provider, model)
    raise ValueError(f"Unknown provider {provider!r}; expected one of {sorted(OPENAI_COMPAT)}")


def cost_usd(provider: str, model: str, input_tokens: int, output_tokens: int) -> float | None:
    if provider in FREE_PROVIDERS:
        return 0.0
    price = PRICES.get(model)
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000
