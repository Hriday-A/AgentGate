import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_MODEL = os.environ.get("AGENTGATE_MODEL", "gemini:gemini-2.5-flash")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentgate", description="Invoice agent + evaluation harness")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run the invoice suite against the reference agent")
    run.add_argument("--model", default=DEFAULT_MODEL, help="provider:model, e.g. ollama:llama3.2")
    run.add_argument("--agent", choices=["graph", "react"], default="graph")
    run.add_argument("--extract-model", help="model for invoice extraction (default: same as --model)")
    run.add_argument("--label", help="name for this configuration in reports")
    run.add_argument("--smoke", action="store_true", help="only the CI smoke subset")
    run.add_argument("--difficulty", choices=["easy", "medium", "hard"])
    run.add_argument("--tasks", help="glob over task ids, e.g. 'hard-acme-*'")
    run.add_argument("--limit", type=int, help="run only the first N matching tasks")
    run.add_argument("--max-steps", type=int, default=30)
    run.add_argument("--concurrency", type=int, default=1)
    run.add_argument("--portal-url", default="http://127.0.0.1:5055")
    run.add_argument("--no-autostart", action="store_true", help="fail instead of starting the portals")
    run.add_argument("--headed", action="store_true", help="show the browser")
    run.add_argument("--out", default="runs")

    cmp_ = sub.add_parser("compare", help="Regression gate between two results.json files")
    cmp_.add_argument("baseline")
    cmp_.add_argument("current")
    cmp_.add_argument("--max-drop", type=float, default=5.0, help="allowed drop in success rate, in points")

    rep = sub.add_parser("report", help="Comparison table (Markdown) across results.json files")
    rep.add_argument("results", nargs="+", type=Path)

    portals = sub.add_parser("portals", help="Serve the mock portals")
    portals.add_argument("--host", default="127.0.0.1")
    portals.add_argument("--port", type=int, default=5055)

    ext = sub.add_parser("extract", help="Extract one invoice PDF and print the JSON")
    ext.add_argument("pdf", type=Path)
    ext.add_argument("--model", default=DEFAULT_MODEL)
    ext.add_argument("--mode", choices=["auto", "text", "image", "both"], default="auto")
    ext.add_argument("--no-repair", action="store_true")

    xev = sub.add_parser("extract-eval", help="Measure extraction accuracy against ground truth")
    xev.add_argument("--model", default=DEFAULT_MODEL)
    xev.add_argument("--n", type=int, default=30, help="number of invoices")
    xev.add_argument("--scanned", type=float, default=0.5, help="share of scanned PDFs")
    xev.add_argument("--mode", choices=["auto", "text", "image", "both"], default="auto")
    xev.add_argument("--no-repair", action="store_true", help="disable validation-guided retries")
    xev.add_argument("--concurrency", type=int, default=2)
    xev.add_argument("--out", default="runs", type=Path)

    args = parser.parse_args(argv)
    if args.cmd == "run":
        from .runner.run import run_suite
        return run_suite(args)
    if args.cmd == "compare":
        from .runner.report import compare
        return compare(args.baseline, args.current, args.max_drop)
    if args.cmd == "report":
        from .runner.report import comparison_table
        print(comparison_table(args.results))
        return 0
    if args.cmd == "extract":
        from .extraction.extract import extract_invoice
        from .llm import make_llm
        result = extract_invoice(args.pdf.read_bytes(), make_llm(args.model), args.mode, not args.no_repair)
        print(json.dumps({
            "invoice": result.invoice.model_dump(mode="json") if result.invoice else None,
            "issues": [i.message for i in result.issues],
            "mode_used": result.mode_used, "attempts": len(result.attempts), "error": result.error,
        }, indent=2))
        return 0 if result.invoice else 1
    if args.cmd == "extract-eval":
        from .extraction import evaluate
        from .extraction.extract import extract_invoice
        from .llm import make_llm
        llm = make_llm(args.model)
        config = {"model": args.model, "mode": args.mode, "repair": not args.no_repair,
                  "n": args.n, "scanned_share": args.scanned}
        results = evaluate.run(
            evaluate.make_samples(args.n, args.scanned),
            lambda pdf: extract_invoice(pdf, llm, args.mode, not args.no_repair),
            config, args.out, args.concurrency)
        evaluate.print_report(results["summary"], config)
        print(f"\nResults: {results['path']}")
        return 0
    import uvicorn
    uvicorn.run("agentgate.portals.app:app", host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
