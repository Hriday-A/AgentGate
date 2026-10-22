"""The agent's tools: their schemas (what the model sees) and their implementation.

Both agent designs (react.py and graph.py) use this same toolbox, so a
comparison between them measures the control loop, not different tools.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page

from ..extraction.extract import ExtractionResult
from ..telemetry import tracer
from .browser import ActionError, Browser


def _tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties or {}, "required": required or []}}


ELEMENT = {"type": "integer", "description": "Element number from the latest snapshot"}

BROWSER_TOOLS = [
    _tool("click", "Click a link, button or other element.", {"element": ELEMENT}, ["element"]),
    _tool("fill", "Set the value of one or more inputs or dropdowns in a single action. For dropdowns, "
                  "give the visible option text.",
          {"fields": {"type": "array", "items": {"type": "object", "properties": {
              "element": ELEMENT, "value": {"type": "string"}}, "required": ["element", "value"]}}},
          ["fields"]),
    _tool("navigate", "Go to a URL on the current site (relative paths allowed).",
          {"url": {"type": "string"}}, ["url"]),
    _tool("go_back", "Go back to the previous page."),
    _tool("read_page", "Read more of the current page's text, starting at a character offset.",
          {"start": {"type": "integer"}}),
    _tool("download", "Click a link that downloads a file (e.g. an invoice PDF) and keep the file.",
          {"element": ELEMENT}, ["element"]),
    _tool("extract_invoice", "Read a downloaded invoice PDF and return its fields as JSON, already formatted "
                             "for data entry (dates YYYY-MM-DD, plain-number amounts).",
          {"filename": {"type": "string"}}, ["filename"]),
]
DONE = _tool("done", "Finish the task. For questions, put only the requested value in `answer`.",
             {"answer": {"type": "string"}}, ["answer"])


@dataclass
class Outcome:
    ok: bool
    message: str
    data: dict | None = None
    target: str | None = None  # label of the element acted on, e.g. "Sign in"


@dataclass
class Toolbox:
    page: Page
    extractor: Callable[[bytes], ExtractionResult] | None = None
    trace_dir: Path | None = None
    browser: Browser = field(init=False)
    downloads: dict[str, bytes] = field(default_factory=dict)
    extractions: dict[str, dict] = field(default_factory=dict)
    extraction_tokens: tuple[int, int] = (0, 0)

    def __post_init__(self) -> None:
        self.browser = Browser(self.page)

    def observe(self) -> str:
        return self.browser.observe()

    def _label(self, element) -> str | None:
        """Human-readable label for an element number in the latest snapshot."""
        snap = self.browser.last
        if snap is None:
            return None
        try:
            return next((e["label"] for e in snap.elements if e["id"] == int(element)), None)
        except (TypeError, ValueError):
            return None

    def run(self, name: str, args: dict) -> Outcome:
        """Run one tool call. Never raises: any problem becomes a failed Outcome the agent can react to."""
        with tracer.start_as_current_span(f"tool.{name}") as span:
            target = None
            try:
                if not isinstance(args, dict):
                    raise ActionError("Tool arguments must be a JSON object.")
                target = self._target(name, args)
                outcome = self._run(name, args)
            except ActionError as e:
                outcome = Outcome(False, str(e))
            except Exception as e:  # never let one bad tool call crash the episode
                outcome = Outcome(False, f"{type(e).__name__}: {e}")
            span.set_attributes({"agentgate.tool.ok": outcome.ok, "agentgate.tool.target": target or ""})
        outcome.target = target or None
        return outcome

    def _target(self, name: str, args: dict) -> str | None:
        if name == "fill":
            fields = args.get("fields") or []
            if not isinstance(fields, list) or not all(isinstance(f, dict) for f in fields):
                raise ActionError('fill needs "fields": a list of {"element": <number>, "value": <text>} objects.')
            return ", ".join(filter(None, (self._label(f.get("element")) for f in fields)))
        return self._label(args.get("element")) if "element" in args else None

    def _run(self, name: str, args: dict) -> Outcome:
        b = self.browser
        if name == "click":
            b.click(args.get("element"))
            return Outcome(True, f"Clicked [{args.get('element')}].")
        if name == "fill":
            fields = args.get("fields") or []
            values = b.fill(fields)
            mismatched = [f"[{f.get('element')}] is {got!r}, wanted {str(f.get('value', ''))!r}"
                          for f, got in zip(fields, values) if got is not None and got != str(f.get("value", ""))]
            if mismatched:  # e.g. a dropdown option that doesn't exist, or a maxlength cut
                return Outcome(False, "Some fields did not take the value: " + "; ".join(mismatched))
            return Outcome(True, f"Filled {len(fields)} field(s).")
        if name == "navigate":
            b.navigate(str(args.get("url", "")))
            return Outcome(True, f"Opened {self.page.url}.")
        if name == "go_back":
            b.go_back()
            return Outcome(True, "Went back.")
        if name == "read_page":
            return Outcome(True, b.read_text(int(args.get("start") or 0)))
        if name == "download":
            filename, data = b.download(args.get("element"))
            if not data.startswith(b"%PDF"):
                return Outcome(False, f"Downloaded {filename}, but it is not a PDF.")
            self.downloads[filename] = data
            if self.trace_dir:
                (self.trace_dir / "downloads").mkdir(parents=True, exist_ok=True)
                (self.trace_dir / "downloads" / filename).write_bytes(data)
            return Outcome(True, f"Downloaded {filename} ({len(data) // 1024} KB). "
                                 f"Use extract_invoice with filename={filename!r}.", {"filename": filename})
        if name == "extract_invoice":
            return self._extract(str(args.get("filename", "")))
        return Outcome(False, f"Unknown tool {name!r}.")

    def _extract(self, filename: str) -> Outcome:
        if filename not in self.downloads:
            have = ", ".join(self.downloads) or "none yet"
            return Outcome(False, f"No downloaded file named {filename!r}. Downloaded: {have}.")
        if self.extractor is None:
            return Outcome(False, "Invoice extraction is not available in this configuration.")
        result = self.extractor(self.downloads[filename])
        tin, tout = self.extraction_tokens
        self.extraction_tokens = (tin + result.input_tokens, tout + result.output_tokens)
        if result.invoice is None:
            return Outcome(False, f"Could not extract {filename}: {result.error}")
        inv = result.invoice
        fields = {
            "vendor_name": inv.vendor_name, "vendor_id": inv.vendor_id, "invoice_number": inv.invoice_number,
            "invoice_date": inv.invoice_date.isoformat(), "due_date": inv.due_date.isoformat(),
            "po_number": inv.po_number, "subtotal": f"{inv.subtotal:.2f}", "tax": f"{inv.tax:.2f}",
            "shipping": f"{inv.shipping:.2f}", "total": f"{inv.total:.2f}",
        }
        self.extractions[filename] = fields
        warning = ("\nValidation warnings (double-check these against the document): " +
                   "; ".join(i.message for i in result.issues)) if result.issues else ""
        return Outcome(True, f"Extracted {filename}:\n{json.dumps(fields, indent=1)}{warning}", fields)
