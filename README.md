# Financial Agent

Upload bank, credit card, loan and investment statements in any format. Get a
reconciled, auditable picture of where your money actually goes — plus loan
amortization, recurring commitments and a cashflow forecast.

Built on **LangGraph** (orchestration), **FastAPI** + **SQLite** (backend),
and **React** + **Recharts** (frontend). Runs entirely on your machine.

---

## The one idea that matters

Most tools of this kind produce numbers that look plausible and are wrong. This
one is built around a single constraint:

> **The arithmetic must tie out to the rupee, and no language model is ever
> allowed to produce a figure.**

Three mechanisms enforce that.

### 1. The reconciliation gate

Every statement declares an opening balance, a closing balance, and rows in
between. If

```
opening + credits − debits ≠ closing
```

the parse is wrong, and the file goes back through extraction with a different
strategy before anything downstream is allowed to use it.

This one check catches the overwhelming majority of extraction errors, and it
diagnoses them:

| Injected fault | What the gate reports |
|---|---|
| One row dropped | "The gap exactly equals a transaction on 2025-09-05 … suggesting one row was duplicated or dropped." |
| One row duplicated | Same, identifying the row |
| Debit read as credit | "The gap is exactly twice the 2025-10-18 transaction of 86,860 — the signature of a debit read as a credit." |
| Decimal point shifted | "Running balance first breaks at source row 80." |
| Trailing pages lost | Flags a discrepancy larger than half the total movement |

### 2. Transfer detection

You upload a bank statement *and* a credit card statement. The bill payment
appears in both. Sum naively and you have inflated spending by the entire bill.
The same applies to EMIs matched against loan statements and SIPs matched
against fund statements.

On the bundled fixtures this removes **₹17.9 lakh** of double counting across 59
matched pairs. Without it, the more diligent you are about uploading everything,
the more wrong your numbers get.

Both legs stay in the ledger — they really happened — but one is flagged as the
*mirror leg* so cashflow counts it exactly once.

### 3. The LLM never does arithmetic

```
PDF/XLSX/DOCX/CSV → extract → normalize → reconcile ┐
                                                     ├→ pandas/Decimal → figures
                          rules → merchant cache ────┘
                                                          ↓
                                            LLM reads finished figures → prose
```

Categorization is rules-first with a learned merchant cache; a model only sees
merchants nothing else recognised, and its answer is cached so it is never asked
twice. **Without an API key the app still works completely** — you lose only the
written narrative and the unknown-merchant tail.

---

## Quick start

```bash
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements.txt
```

```bash
.venv/Scripts/python backend/tools/generate_samples.py
```

Start the API (terminal 1):

```bash
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8078 --app-dir backend
```

Start the UI (terminal 2):

```bash
npm install --prefix frontend && npm run dev --prefix frontend
```

Open <http://localhost:5173> and drop the files from `data/samples/` onto the
upload area.

Optional — for the written narrative, copy `.env.example` to `.env` and add an
`ANTHROPIC_API_KEY`.

---

## What it produces

- **Overview** — income, spending, savings rate, net position, and the narrative
- **Spending** — category and merchant breakdowns, per-month trends, outliers,
  and *"after the salary landed, where did it go?"* traced between paydays
- **Debt** — amortization per loan, payoff dates, total interest remaining, and
  how much of the *next* EMI is interest rather than principal
- **Forecast** — committed vs discretionary cashflow with a low/expected/high
  band, runway, and the first projected shortfall month
- **Transactions** — full ledger; recategorizing one teaches the merchant
  permanently
- **Files & quality** — per-file reconciliation status and every matched transfer

---

## Protected PDFs, deduplication, and Gmail import

**Password-protected statements open automatically.** Enter your details once in
**Profile** (name, date of birth, PAN, mobile). Indian banks build statement
passwords from these — the classic format is the first four letters of your name
plus your date of birth, e.g. `jite0602`. The app generates a small, bounded set
of candidates *from those templates* and tries them against your own protected
files. This is not password cracking: it only ever runs on files you uploaded,
uses only your own details, generates dozens of format-based candidates (never a
brute-force space), and a wrong guess simply moves on. Your PII stays in the
local database, is used only to open files, and never reaches any model or
network call. A working password is logged only in redacted form (`j*******`).

**Deduplication is content-based, at three layers.** The same file added twice —
*even renamed* — is caught by its content hash and counted once. Statements that
merely overlap (a monthly and a quarterly covering the same weeks) are caught at
the transaction level. And an identical file re-uploaded in a later session
replaces its old rows via a unique-hash index rather than doubling them.

**Import straight from Gmail** (optional). Instead of downloading every
statement yourself, connect Gmail and the app finds your bank/card statement
emails, downloads the PDF attachments, and analyzes them. The security model:

- **OAuth, read-only.** Sign-in happens on Google's own consent screen — the app
  never sees your Gmail password. The scope is `gmail.readonly`, so it can read
  and download but can never send, delete, or modify mail.
- **You review before anything downloads.** *Scan* lists what it found; you tick
  which statements to import; only then are they pulled.
- **Local token.** The OAuth token lives in a local file and is used only from
  your machine.

Without setup, the manual upload path is unaffected. The whole fetch → filter →
download → parse path is covered by offline tests using a fake Gmail client, so
it's verifiable without a Google account.

### Connecting Gmail

**What `credentials.json` actually is.** It is *not* your password, and it holds
none of your personal data. It's a small file from Google that identifies *this
app* to Google — like an app's ID card. When you later click "Connect Gmail",
Google shows you its own sign-in page; you approve there, and Google hands back
a token. Your Gmail password never touches this code.

Google requires this because it won't let arbitrary software ask for your mail —
the app has to be registered first. Registering is free and takes a few minutes.

**Step by step:**

1. Go to <https://console.cloud.google.com> and sign in.
2. Create a project (top bar → project dropdown → **New Project**). Name it
   anything, e.g. `financial-agent`.
3. **Enable the Gmail API.** Search "Gmail API" in the top search bar, open it,
   click **Enable**. (Skip this and connecting fails with "Gmail API has not
   been used in project…".)
4. **Configure the consent screen.** Find *OAuth consent screen* (newer consoles
   call this **Google Auth Platform → Branding / Audience**).
   - User type: **External**
   - Fill in app name and your email where asked
   - Under **Audience / Test users**, click **Add users** and add your own Gmail
     address. Without this, Google blocks your own sign-in.
5. **Create the client.** Go to **Credentials** (or *Google Auth Platform →
   Clients*) → **Create Credentials** → **OAuth client ID**.
   - Application type: **Desktop app** ← this matters; a "Web application"
     client will fail at the redirect step
   - Click **Create**, then **Download JSON**
6. Save that file as `credentials.json` in the project root, next to `README.md`:
   `D:\python\financial-agent\credentials.json`

**Check it worked:**

```bash
.venv/Scripts/python backend/tools/check_gmail_setup.py
```

This tells you exactly what's wrong if anything is — wrong client type, service
account key by mistake, malformed file, or missing fields.

7. Start the app, open the upload screen, click **Connect Gmail**. A Google page
   opens in your browser. Google will warn the app is *unverified* — that's
   expected, it's your own private app, published to nobody. Choose
   **Advanced → Go to \<app name\> (unsafe)** and approve the read-only access.
8. Click **Scan for statements**, review the list, tick what you want, and
   **Import & analyze**.

**To disconnect:** delete `data/gmail_token.json`. That revokes this app's
access locally; you can also remove it from your Google account at
<https://myaccount.google.com/permissions>.

## Supported formats

`.pdf` `.xlsx` `.xlsm` `.xls` `.csv` `.tsv` `.txt` `.docx`

Format is detected from content (magic bytes), not the extension. PDFs go
through a strategy ladder — ruled-table extraction, then whitespace-aligned,
then raw text-line parsing — stopping at the first that yields a table that
reconciles.

---

## The LangGraph structure

```
START → plan_ingestion
          │
          ├── Send(per file) ──→ ingest_file  (parallel: extract → normalize → reconcile)
          │                          │
          │                   route_after_ingestion
          │                     │              │
          │            retry_extraction    merge_ledger      ←── the reconciliation cycle
          │                     └── Send ────┘
          ↓
     merge_ledger → detect_transfers → categorize_rules
                                            │
                                  route_after_rules
                                     │              │
                              categorize_llm   finalize_categories
                                     └──────┬───────┘
                                            ↓
                          detect_recurring → run_analytics → project_loans
                                            → build_forecast → synthesize → END
```

Each feature earns its place:

| Feature | Why it's needed here |
|---|---|
| `Send` fan-out | 40 statements parse concurrently |
| Conditional edges + cycle | A statement that doesn't balance goes back through extraction |
| Reducers (`operator.add`) | Parallel branches merge into one `statements` list |
| Route-around | Skip the model entirely when rules resolved everything |
| SQLite checkpointer | Ten years of statements is slow; a crash must not discard the parsing work |

Nodes are deliberately thin. All real logic lives in `ingestion/`,
`normalize/`, `reconcile/`, `categorize/` and `analytics/` as plain functions
that know nothing about LangGraph — so the business logic is unit-testable
without a graph, and the graph reads as a flowchart.

---

## Layout

```
backend/app/
  models/schemas.py      Canonical domain model (all money is Decimal)
  ingestion/             Format detection + per-format extractors
  normalize/             Date/amount parsers, column mapping, metadata, normalizer
  reconcile/             The balance gate + transfer detection
  categorize/            Rules engine, merchant cache, LLM tail
  analytics/             Cashflow, recurring, loans, forecast
  graph/                 LangGraph state, nodes, assembly
  llm/                   Anthropic client (with redaction) + narrative
  db/                    SQLite schema and repository
  api/, main.py          FastAPI
backend/tools/           Synthetic fixture generator
backend/tests/           56 tests, including fault injection
frontend/src/            React UI
```

## Tests

```bash
.venv/Scripts/python -m pytest backend/tests -q
```

The fixtures are generated from a 12-month simulation with **known ground
truth**, so tests assert against independently derivable figures rather than
whatever the code happened to produce. Key invariants:

- every format parses to the exact expected row count and balances
- XLSX and CSV exports of the same account produce identical ledgers
- the gate catches all five fault classes
- a credit-card statement reconciled with the asset formula **fails** (sign
  conventions can't silently pass)
- SIPs appear on two statements but total ₹4,80,000 — not ₹9,60,000
- per-month category totals sum exactly to the period breakdown

---

## Privacy and scope

Everything runs locally; the SQLite file never leaves your machine. Account
numbers are masked to the last four digits at ingestion and never stored in
full. Text is redacted again before any model call.

This tool reports facts about your own statements and states mechanical
trade-offs. It does not give personalized investment advice, and the system
prompt forbids the model from doing so. For decisions about prepaying,
investing or restructuring debt, talk to a qualified adviser who can see your
whole picture.
