"""Spans are emitted for model calls and the trace context travels to the API."""
import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentgate.middleware import MeteredLLM
from agentgate.runner.publish import publish
from test_middleware import Fake, graph_prompt
from test_publish import make_run

EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def provider():
    p = TracerProvider()
    p.add_span_processor(SimpleSpanProcessor(EXPORTER))
    trace.set_tracer_provider(p)  # global, once per process
    yield


@pytest.fixture(autouse=True)
def clear():
    EXPORTER.clear()


def test_model_calls_become_spans_with_genai_attributes():
    MeteredLLM(Fake("gemini:large")).complete("s", graph_prompt("Sign in"), [])
    (span,) = EXPORTER.get_finished_spans()
    assert span.name == "llm.complete"
    assert span.attributes["gen_ai.request.model"] == "gemini:large"
    assert span.attributes["gen_ai.usage.input_tokens"] == 100


def test_publish_sends_traceparent_to_the_api(tmp_path):
    seen = []

    def handler(request):
        seen.append(request.headers.get("traceparent"))
        if request.url.path == "/graphql":
            return httpx.Response(200, json={"data": {"ingestRun": {"id": "1"}}})
        return httpx.Response(201)

    publish(make_run(tmp_path), "http://api", "ag_k", httpx.Client(transport=httpx.MockTransport(handler)))
    trace_id = format(EXPORTER.get_finished_spans()[-1].context.trace_id, "032x")
    assert seen and all(h and h.split("-")[1] == trace_id for h in seen)  # every request carries the publish trace
