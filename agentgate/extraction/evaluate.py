"""Extraction eval: per-field accuracy against ground truth, sliced by template and clean/scanned.

No browser or portal server is involved. Invoices and PDFs come straight from
the environment generator, so every sample has exact ground truth.
"""
from __future__ import annotations

import json
import random
import statistics
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..portals import data
from ..portals.friction import resolve
from ..portals.pdfs import render_invoice_pdf
from .extract import ExtractionResult
from .schema import Invoice, cents

FIELDS = ["vendor_id", "invoice_number", "invoice_date", "due_date", "po_number",
          "subtotal", "tax", "shipping", "total", "line_items"]


@dataclass
class Sample:
    id: str
    seed: int
    invoice: data.Invoice

    @property
    def template(self) -> str:
        return data.VENDORS[self.invoice.vendor]["pdf_template"]

    def pdf(self) -> bytes:
        return render_invoice_pdf(self.invoice, self.seed)


def make_samples(n: int, scanned_share: float = 0.5, start_seed: int = 1000) -> list[Sample]:
    """A fixed, balanced sample: vendors round-robin, scans interleaved at the requested share."""
    samples = []
    vendors = list(data.VENDORS)
    for i in range(n):
        seed = start_seed + i // len(vendors)
        vendor = vendors[i % len(vendors)]
        invoices = data.generate_env("eval", seed, resolve("easy")).invoices[vendor]
        inv = invoices[random.Random(f"{seed}:pick:{vendor}").randrange(len(invoices))]
        # Scanned exactly when the running share would otherwise fall below the target.
        inv.scanned = sum(s.invoice.scanned for s in samples) < round(scanned_share * (i + 1))
        samples.append(Sample(f"{vendor}-{seed}-{inv.number}", seed, inv))
    return samples


def truth(inv: data.Invoice) -> dict:
    return {
        "vendor_id": data.VENDORS[inv.vendor]["vendor_id"],
        "invoice_number": inv.number,
        "invoice_date": inv.issue_date.isoformat(),
        "due_date": inv.due_date.isoformat(),
        "po_number": inv.po_number,
        "subtotal": inv.subtotal_cents,
        "tax": inv.tax_cents,
        "shipping": inv.shipping_cents,
        "total": inv.amount_cents,
        "line_items": [(l["qty"], l["unit_cents"], l["qty"] * l["unit_cents"]) for l in inv.lines],
    }


def normalize(inv: Invoice) -> dict:
    return {
        "vendor_id": inv.vendor_id.strip(),
        "invoice_number": inv.invoice_number.strip(),
        "invoice_date": inv.invoice_date.isoformat(),
        "due_date": inv.due_date.isoformat(),
        "po_number": inv.po_number.strip(),
        "subtotal": cents(inv.subtotal),
        "tax": cents(inv.tax),
        "shipping": cents(inv.shipping),
        "total": cents(inv.total),
        "line_items": [(round(i.quantity), cents(i.unit_price), cents(i.amount)) for i in inv.line_items],
    }


def score(inv: Invoice | None, expected: dict) -> dict[str, bool]:
    if inv is None:
        return {f: False for f in FIELDS}
    got = normalize(inv)
    return {f: got[f] == expected[f] for f in FIELDS}


def evaluate_sample(sample: Sample, extractor: Callable[[bytes], ExtractionResult]) -> dict:
    result = extractor(sample.pdf())
    expected = truth(sample.invoice)
    fields = score(result.invoice, expected)
    first = score(result.first.invoice if result.first else None, expected)
    return {
        "id": sample.id,
        "vendor": sample.invoice.vendor,
        "template": sample.template,
        "scanned": sample.invoice.scanned,
        "mode_used": result.mode_used,
        "fields": fields,
        "correct": all(fields.values()),
        "first_attempt_correct": all(first.values()),
        "flagged": bool(result.issues),
        "issues": [i.code for i in result.issues],
        "attempts": len(result.attempts),
        "seconds": round(result.seconds, 3),
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "error": result.error,
        "extracted": result.invoice.model_dump(mode="json") if result.invoice else None,
    }


def _rate(xs: list[bool]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def _pct(q: float, xs: list[float]) -> float:
    if len(xs) < 2:
        return xs[0] if xs else 0.0
    return round(statistics.quantiles(xs, n=100, method="inclusive")[int(q) - 1], 3)


def summarize(records: list[dict]) -> dict:
    def block(rs: list[dict]) -> dict:
        return {
            "n": len(rs),
            "doc_accuracy": _rate([r["correct"] for r in rs]),
            "first_attempt_accuracy": _rate([r["first_attempt_correct"] for r in rs]),
            "field_accuracy": _rate([v for r in rs for v in r["fields"].values()]),
        }

    wrong = [r for r in records if not r["correct"]]
    flagged = [r for r in records if r["flagged"]]
    seconds = [r["seconds"] for r in records]
    return {
        **block(records),
        "per_field": {f: _rate([r["fields"][f] for r in records]) for f in FIELDS},
        "slices": {
            f"{t}/{'scanned' if s else 'clean'}": block([r for r in records if r["template"] == t and r["scanned"] == s])
            for t in sorted({r["template"] for r in records}) for s in (False, True)
            if any(r["template"] == t and r["scanned"] == s for r in records)
        },
        # Validator quality: does a failed check actually mean the extraction is wrong, and vice versa?
        "validator_recall": _rate([r["flagged"] for r in wrong]),       # wrong docs that got flagged
        "validator_precision": _rate([not r["correct"] for r in flagged]),  # flagged docs that were wrong
        "avg_attempts": round(statistics.mean(r["attempts"] for r in records), 2) if records else 0,
        "p50_seconds": _pct(50, seconds),
        "p95_seconds": _pct(95, seconds),
        "input_tokens": sum(r["input_tokens"] for r in records),
        "output_tokens": sum(r["output_tokens"] for r in records),
        "errors": sum(r["error"] is not None for r in records),
    }


def _fmt(x: float | None) -> str:
    return "  -  " if x is None else f"{x:6.1%}"


def print_report(summary: dict, config: dict) -> None:
    print(f"\nExtraction eval · {config['model']} · mode={config['mode']} · scans={config.get('scans', 'image')} · "
          f"amounts={config.get('amounts', 'number')} · repair={config['repair']} · n={summary['n']}")
    print(f"\n{'SLICE':<22} {'N':>3} {'DOC ACC':>8} {'1ST TRY':>8} {'FIELDS':>8}")
    for name, b in summary["slices"].items():
        print(f"{name:<22} {b['n']:>3} {_fmt(b['doc_accuracy']):>8} {_fmt(b['first_attempt_accuracy']):>8} "
              f"{_fmt(b['field_accuracy']):>8}")
    print(f"{'ALL':<22} {summary['n']:>3} {_fmt(summary['doc_accuracy']):>8} "
          f"{_fmt(summary['first_attempt_accuracy']):>8} {_fmt(summary['field_accuracy']):>8}")
    print("\nPer field: " + "  ".join(f"{f}={_fmt(v).strip()}" for f, v in summary["per_field"].items()))
    print(f"Validator: recall {_fmt(summary['validator_recall']).strip()} · precision "
          f"{_fmt(summary['validator_precision']).strip()}   Attempts avg {summary['avg_attempts']}")
    print(f"Latency p50 {summary['p50_seconds']}s · p95 {summary['p95_seconds']}s   "
          f"Tokens {summary['input_tokens']}/{summary['output_tokens']}   Errors {summary['errors']}")


def run(samples: list[Sample], extractor: Callable[[bytes], ExtractionResult], config: dict,
        out_dir: Path | None = None, concurrency: int = 1) -> dict:
    def one(sample: Sample) -> dict:
        record = evaluate_sample(sample, extractor)
        mark = "✓" if record["correct"] else ("!" if record["error"] else "✗")
        print(f"  {mark} {record['id']}{' (scan)' if record['scanned'] else ''}", flush=True)
        return record

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        records = list(pool.map(one, samples))
    summary = summarize(records)
    results = {"kind": "extraction", "config": config, "summary": summary, "records": records}
    if out_dir:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = out_dir / "extraction" / run_id
        path.mkdir(parents=True, exist_ok=True)
        (path / "results.json").write_text(json.dumps(results, indent=2))
        results["path"] = str(path / "results.json")
    return results
