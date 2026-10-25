# AgentGate

An AI agent that processes invoices across messy vendor portals, and the evaluation platform that measures and
improves its reliability. The agent signs in to simulated vendor portals, downloads invoice PDFs, extracts and
validates the fields, and records them in an accounts-payable app. Every run is scored automatically, gated in
CI, and browsable step by step in a dashboard.

## Architecture

```mermaid
flowchart LR
  subgraph Python
    P[Mock portals<br/>FastAPI] --- A[Agent<br/>LangGraph + Playwright]
    A --> X[Extraction<br/>Pydantic + validation]
    R[Eval runner<br/>54 tasks] --> A
  end
  R -- "ingestRun (X-Api-Key)" --> API
  subgraph Platform
    API[Spring Boot<br/>GraphQL API] --> DB[(PostgreSQL)]
    D[Next.js dashboard<br/>Apollo Client] -- "/graphql (session)" --> API
  end
  CI[GitHub Actions] -- "smoke eval + gate" --> R
```

| Part | Stack | Where |
|---|---|---|
| Test environment | FastAPI, Jinja, reportlab | [`agentgate/portals`](agentgate/portals) |
| Agent | LangGraph, Playwright, OpenAI-compatible LLM layer | [`agentgate/agent`](agentgate/agent), [`agentgate/llm.py`](agentgate/llm.py) |
| Document extraction | Pydantic structured output, rule validation | [`agentgate/extraction`](agentgate/extraction) |
| Eval suite and CI gate | pytest, GitHub Actions | [`agentgate/suites`](agentgate/suites), [`agentgate/runner`](agentgate/runner), [`.github/workflows`](.github/workflows) |
| API | Spring Boot 4, Spring for GraphQL, JPA, Flyway, PostgreSQL | [`api`](api) |
| Dashboard | Next.js 16, React 19, TypeScript, Apollo Client 4, Jest | [`dashboard`](dashboard) |

## Quickstart

**Whole stack with Docker:** `docker compose up --build`, then open http://localhost:3000 (dev sign-in is on).

**Python only:**

```bash
make setup                                   # venv, dependencies, Playwright Chromium
make test                                    # 220+ tests, no API key needed
export GEMINI_API_KEY=...                    # free key from Google AI Studio
python -m agentgate run --smoke              # 6-task smoke run against the graph agent
python -m agentgate run --difficulty hard    # or a slice of the 54-task suite
python -m agentgate extract-eval --n 30      # extraction accuracy on clean + scanned PDFs
python -m agentgate report runs/*/results.json   # Markdown comparison table across runs
```

The default model is `gemini:gemma-4-26b-a4b-it`: free, supports tool calls, and has a much larger free quota
than `gemini-3.5-flash` (20 requests a day, not enough for one smoke run). Any OpenAI-compatible provider works
via `--model provider:model`: `gemini:…`, `ollama:…` (local), `groq:…`, `openrouter:…`, `hf:…`.

**API and dashboard without Docker** (needs Java 21, Node 22, PostgreSQL):

```bash
createdb agentgate
cd api && DEV_LOGIN=true ./mvnw spring-boot:run          # http://localhost:8080
cd dashboard && npm install && npm run dev               # http://localhost:3000
```

Sign in, register an agent to get an API key, then publish runs:

```bash
export AGENTGATE_API_KEY=ag_...
python -m agentgate publish runs/<run-id>
```

## The test environment

Each task run gets an isolated environment from the portal admin API, generated from a seed. **Friction** is
toggled per environment:

| Toggle | What the agent faces | easy | medium | hard |
|---|---|:-:|:-:|:-:|
| `shuffle_layout` | Column order, labels ("Gross", "Pay By"), status wording, sort order | | ✓ | ✓ |
| `renamed_buttons` | "Sign in" → "Access account", "View" → "›", "Download PDF" → "Export"… | | ✓ | ✓ |
| `pagination` | Invoice list split into pages of 4–6 | | ✓ | ✓ |
| `popups` | Cookie banner on every page, survey modal over the invoice list | | ✓ | ✓ |
| `session_timeout` | Session expires after N page loads | | | ✓ |
| `scanned_pdf_rate` | Share of image-only PDFs: rotated, noisy, blurred, no text layer | 0 | 0.2 | 0.5 |

- **Data and presentation use separate randomness**, so the same seed gives identical invoices at every
  difficulty, and any change in success rate comes from friction alone.
- **Sessions expire by page loads, not time**, so slow and fast models hit the timeout at the same point.
- **PDFs are byte-for-byte deterministic**: three vendor templates (US, bilingual German/English, statement
  with a "previous balance" distractor) plus scanned variants.
- **The portal shows a summary only**: PO number, subtotal, tax and shipping are in the PDF, so the agent
  must download and extract it.

## The agent

```mermaid
flowchart LR
  START --> plan --> execute --> act --> verify
  verify -- "ok, or failed < 3×" --> execute
  verify -- "3 failures in a row" --> plan
  verify -- "done / out of steps / stuck" --> END
```

- **plan:** the LLM writes a 3–8 step plan, or re-plans the remaining work after repeated failures (2× max).
- **execute:** the LLM picks one tool for the current step: `click`, `fill`, `navigate`, `go_back`,
  `read_page`, `download`, `extract_invoice`, `step_done`, `done`.
- **verify:** deterministic checks, no LLM call. Did the tool fail? Did an error alert appear (bad login,
  expired session, form validation)? Did a click change nothing? Failures are fed back as feedback.

A ReAct baseline (`--agent react`) uses the same tools, so comparisons measure the control loop only.

**Extraction** sends the PDF's text layer, or page images for scans, to the model with a Pydantic-derived JSON
schema. It then validates line math, totals, dates, the vendor master and the tax rate, and retries with the
failed checks as feedback. `extract-eval` reports per-field accuracy by template and clean/scanned, accuracy
before and after repair, and validator precision and recall.

## Evaluation and CI

- **54 tasks:** 6 kinds (lookups, counts, totals, data entry, find-oldest-and-enter) × 3 vendors × 3
  difficulties. Ground truth comes from the environment generator, and data-entry tasks score each AP field.
- **Metrics:** success rate (overall and per difficulty), field accuracy, steps, p50/p95 latency, tokens,
  cost per task, and failure reasons.
- **CI** ([`ci.yml`](.github/workflows/ci.yml)): Python, Java (against PostgreSQL), dashboard, and Docker
  builds on every push. On pull requests, the smoke subset runs against a real model and **fails the build if
  success drops** versus [`baselines/smoke.json`](baselines). Needs a `GEMINI_API_KEY` repository secret.

## Scaling: routing, caching, learning, tracing

Every model call passes through a small middleware stack ([`middleware.py`](agentgate/middleware.py)):

```
agent ─► Metered (ledger + OTel span) ─► Routed ─┬─► Cached ─► small local model   (easy steps)
                                                  └─► Cached ─► large API model     (everything else)
```

- **Routing** (`--small-model ollama:qwen2.5:1.5b`): sign-in, pop-up, navigation and download steps go to a
  small local model. Planning, forms, extraction and any step after a failure go to the large model. A small
  model that returns no action escalates to the large one. `--router bandit` replaces the rules with an
  epsilon-greedy contextual bandit that learns per step type which model passes verification, with a latency
  penalty.
- **Caching** (`--cache`): exact-match SQLite cache of model responses. Environments use deterministic ids, so
  an unchanged task replays identical prompts and costs nothing on a CI re-run.
- **Feedback loop** (`python -m agentgate learn --rounds 3`): failed tasks get a reflection, where the model
  reads its own trace and writes up to 3 general lessons. Passed tasks become demonstrations. Lessons are
  retrieved for new tasks by vendor, task kind and word overlap. Each round runs on **fresh seeds** (new
  invoices and layouts), the reflection never sees the expected answer, and lessons containing specific
  numbers are discarded, so gains have to come from generalizable lessons.
- **Tracing**: OpenTelemetry spans for run → task → agent node → tool → model call (GenAI attributes: model,
  tokens), exported over OTLP. `publish` sends a W3C `traceparent` header, so the Spring API's spans join
  the runner's trace. Docker Compose includes Jaeger at http://localhost:16686.

## Platform

- **API:** GraphQL schema in [`schema.graphqls`](api/src/main/resources/graphql/schema.graphqls). Agents get
  one-time API keys, stored as SHA-256 hashes, for ingest. People sign in with GitHub OAuth (or the opt-in
  dev login). Every query is scoped to the caller's own agents, so other users' data reads as "not found".
  Re-ingesting a run updates it in place. Screenshot storage is protected against path traversal.
- **Dashboard:** agents with their latest results, a success trend chart per agent, a run view with
  per-difficulty bars and failed-task filter, and a step-by-step replay of every task with the screenshot
  after each action and the verifier's feedback.

### GitHub OAuth

Create a GitHub OAuth App with callback `http://localhost:3000/login/oauth2/code/github`, then start the API with
`SPRING_PROFILES_ACTIVE=github GITHUB_CLIENT_ID=… GITHUB_CLIENT_SECRET=…`.

## Results

All numbers below are measured with the commands above, on free models: `gemma-4-26b-a4b-it` through the
Gemini API free tier, and `qwen2.5:1.5b` running locally in Ollama (with `num_ctx 8192`; Ollama's default context
truncates page snapshots). Samples are small (6 tasks per run), so
one task is ~17 points; treat single-run differences of one task as noise.

**Agent, 6-task smoke set** (`run --smoke`, easy + medium):

| Configuration | Success | Easy | Medium | Avg steps | p50 / task | Model calls |
|---|--:|--:|--:|--:|--:|--:|
| ReAct baseline | **6/6** | 3/3 | 3/3 | 10.3 | 225 s | 68 |
| Graph agent, first version | 4/6 | 3/3 | 1/3 | 17.5 | 361 s | 117 |
| Graph agent + notes and loop rules (2 runs) | 5/6, 4/6 | 3/3 | 2/3, 1/3 | 20.7 | 328 s | 143 |
| Graph + routing (easy steps on local 1.5B) | 4/4 ran | 1/1 | 3/3 | 15.8 | 416 s | 105 (21 local) |

- **The simpler agent won.** With this model, ReAct (full conversation history) beat the planner/executor.
  The graph agent's executor sees only the current page, so on paginated lists it bounced between page 1
  and 2 until it ran out of steps. A `note` tool, a verifier rule for A→B→A→B ping-pong and an exact-repeat
  rule fixed those loops, but medium tasks still sometimes exhaust the 30-step budget.
- **Routing:** with easy steps on the local model (fail fast, escalate on any error), every task that ran
  passed, and a fifth of model calls never left the laptop. Two tasks in that run hit a harness error
  (two runs shared one portal server) and are excluded.
- `baselines/smoke.json` is the graph agent's 4/6 run; the CI gate fails a PR that drops more than 20 points.

**Feedback loop** (`learn --rounds 3 --difficulty hard --limit 6`, fresh seeds every round):

| Round | Lessons in memory | Hard-task success |
|---|--:|--:|
| 1 | 0 | 4/6 (67%) |
| 2 | 4 | **6/6 (100%)** |
| 3 | 6 | 4/5 finished (one task stopped after repeated model timeouts) |

**Extraction, local `qwen2.5:1.5b` + Tesseract OCR** (30 invoices, half scanned, `--scans ocr`):

| | Model returns numbers | Model copies printed amounts, code parses them (`--amounts text`) |
|---|--:|--:|
| Whole document correct | 43.3% | **46.7%** |
| Field accuracy | 92.3% | **94.0%** |
| Subtotal / total | 93.3% / 93.3% | **100% / 100%** |
| Scanned statements | 0% | 33.3% |
| Line items | 46.7% | 46.7% |
| Validator recall / precision | 100% / 100% | 100% / 100% |

The validator flagged every incorrect document, so none would be entered silently. Line-item tables are
what a 1.5B model can't copy reliably. With `gemini-3.5-flash`, 11/11 documents (6 of them scanned) were fully
correct before the free-tier daily quota ran out.

**Response cache:** the same task twice took 58.0 s → 1.6 s, with 7/7 model calls served from cache and 0
tokens on the re-run.

## Repository layout

```
agentgate/      Python package: portals, agent, extraction, suites, runner, LLM layer
api/            Spring Boot GraphQL API (Java 21)
dashboard/      Next.js dashboard (TypeScript)
tests/          pytest (unit, integration, browser)
baselines/      CI gate reference results
```

## Note for macOS + Python 3.13

The Makefile calls `python -m agentgate` from the repo root instead of the `agentgate` script. On some Macs,
files inside `.venv` get the hidden flag, and Python 3.13 skips hidden `.pth` files, which breaks editable
installs.
