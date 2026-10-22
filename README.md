# AgentGate

An AI agent that processes invoices across messy vendor portals, plus the evaluation suite that measures and
improves its reliability. The agent signs in to simulated vendor portals, downloads invoice PDFs, extracts and
validates the fields, and records them in a separate accounts-payable app. Every run is scored automatically.

## Repository layout

```
agentgate/                 Python package
  portals/                 Test environment (FastAPI): 3 vendor portals + AP app      ← Phase 1 ✅
    friction.py            Toggleable obstacles and easy/medium/hard presets
    data.py                Seeded invoice data and per-vendor layouts
    pdfs.py                Invoice PDF templates and the "scanned" variant
    vendor.py, ap.py       Routers for the vendor portals and the AP app
    app.py, state.py       Admin API, app wiring, environment store
  agent/                   Playwright + LLM tool-calling agent (LangGraph planner next)  ← Phase 1
  extraction/              PDF → Pydantic schema + validation                      ← Phase 1 (next)
  suites/                  Task definitions and checkers
  runner/                  Eval runner, metrics, comparison table, CI gate
tests/                     pytest
api/                       Spring Boot + Spring for GraphQL + PostgreSQL           ← Phase 2
dashboard/                 Next.js + React + TypeScript + Apollo                   ← Phase 2
.github/workflows/         Eval regression gate on every pull request              ← Phase 1
docker-compose.yml                                                                 ← Phase 2
```

## Quickstart

```bash
make setup     # venv, dependencies, Playwright Chromium
make test      # pytest: portals, PDFs, AP validation, checkers
make portals   # http://127.0.0.1:5055 → pick an easy / medium / hard demo environment
```

## The test environment

Each task run gets an isolated environment from the admin API:

```bash
curl -X POST localhost:5055/admin/envs -H 'Content-Type: application/json' \
     -d '{"seed": 7, "difficulty": "hard", "friction": {"session_max_requests": 5}}'
```

Interactive API docs are at `/admin/docs`. `GET /admin/envs/{id}/state` returns the ground truth (every
invoice and every AP entry) that the checkers score against. The agent never calls the admin API.

### Friction

| Toggle | What the agent faces | easy | medium | hard |
|---|---|:-:|:-:|:-:|
| `shuffle_layout` | Column order, column labels ("Gross", "Pay By"), status wording, sort order | | ✓ | ✓ |
| `renamed_buttons` | "Sign in" → "Access account", "View" → "›", "Download PDF" → "Export"… | | ✓ | ✓ |
| `pagination` | Invoice list split into pages of 4–6 | | ✓ | ✓ |
| `popups` | Cookie banner on every page, survey modal over the invoice list | | ✓ | ✓ |
| `session_timeout` | Session expires after `session_max_requests` page loads (default 8) | | | ✓ |
| `scanned_pdf_rate` | Share of PDFs that are image-only scans: rotated, noisy, blurred, no text layer | 0 | 0.2 | 0.5 |

Design choices worth knowing:

- **Data and presentation use separate randomness.** Invoices depend only on the seed, so the same seed gives
  identical invoices at every difficulty, and any change in success rate comes from friction alone.
- **Sessions expire by page loads, not wall-clock time.** A slow model and a fast model hit the timeout at the
  same point in the task, which keeps runs comparable and reproducible.
- **Scans are monotonic.** Raising `scanned_pdf_rate` only adds scans, so every invoice scanned at medium is
  also scanned at hard.
- **PDFs are byte-for-byte deterministic,** so a failed run can be replayed against the exact same document.

### Vendor portals and PDFs

| Vendor | Dates | Money | PDF template |
|---|---|---|---|
| Acme Supply Co. | 05/23/2026 | $37,756.63 | US "INVOICE", labeled meta block, TOTAL DUE |
| Globex Industrial GmbH | 06.09.2026 | 31.026,72 USD | Bilingual German/English, boxed fields, 19% VAT |
| Initech Office Solutions | Apr 25, 2026 | USD 29323.68 | Monospaced statement, "previous balance" distractor |

The portal's invoice page shows only a summary: dates, total and status. The PO number, subtotal, tax and
shipping appear only in the PDF.

### Accounts-payable app

The AP app requires vendor, invoice number, invoice date, due date, PO number, subtotal, tax, shipping and
total. It rejects non-ISO dates, currency symbols, a due date before the invoice date, totals that don't add
up, and duplicate invoices.

## Note for macOS + Python 3.13

The Makefile calls `python -m agentgate` from the repo root instead of the `agentgate` script. On some Macs,
files inside `.venv` get the hidden flag, and Python 3.13 skips hidden `.pth` files, which breaks editable
installs.
