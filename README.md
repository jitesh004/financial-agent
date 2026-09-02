# Financial Agent

Upload bank, credit card, loan and investment statements in any format. Get a
reconciled, auditable picture of where your money actually goes — plus loan
amortization, recurring commitments and a cashflow forecast.

Built on **LangGraph** (orchestration), **FastAPI** + **PostgreSQL** (backend),
and **React** + **Recharts** (frontend). Sign in with Google; every account's
ledger is isolated from every other by the database itself.

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

The fastest path — PostgreSQL, the API and the UI together:

```bash
cp .env.example .env          # then fill in the two Google values
docker compose up --build
```

`--build` matters on the first run and after every `git pull`: `docker compose
up` on its own reuses whatever image it already has, so a backend image built
before the PostgreSQL migration starts and then dies on `import psycopg` -
a dependency that is sitting right there in `requirements.txt`.

Open <http://localhost:5173>, sign in, and the setup wizard takes it from
there.

### Or run it without Docker

You need a PostgreSQL 15 or newer server. Create the database and the
**ordinary, non-superuser role** the app connects as:

```bash
psql -U postgres -f deploy/postgres-init.sql
```

That role matters. PostgreSQL exempts superusers — and any role holding
`BYPASSRLS` — from every row-level security policy, and those policies are the
whole of this app's per-user separation. Connect as one and everybody sees
everybody's statements; the app checks at startup and refuses to serve rather
than run that way.

```bash
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements.txt
cp .env.example .env
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

Open <http://localhost:5173>, sign in, and drop the files from `data/samples/`
into the import wizard.

Optional — for the written narrative, add a `GEMINI_API_KEY` to `.env`.

---

## Signing in

Sign-in is Google OAuth, and it asks for **identity only** — `openid email
profile`, which is your name and email address and nothing else. Reading your
mailbox is a *separate* permission, requested later during setup and
declinable without losing anything but the mailbox import.

You supply your own Google OAuth client, so no third party is ever in the
middle:

1. Go to <https://console.cloud.google.com> and create a project.
2. **Credentials → Create credentials → OAuth client ID.**
   Application type: **Web application** — this matters; a Desktop client
   rejects the redirect and says almost nothing about why.
3. Add `http://localhost:5173/api/auth/google/callback` under *Authorised
   redirect URIs* (in production, the same path on your own domain, over
   https).
4. Put the client id and secret in `.env` as `GOOGLE_CLIENT_ID` and
   `GOOGLE_CLIENT_SECRET`, and set `FA_APP_BASE_URL` to where the browser
   reaches the app.

Check it with:

```bash
.venv/Scripts/python backend/tools/check_gmail_setup.py
```

By default anyone with a Google account can sign up. Set `FA_ALLOWED_SIGNINS`
to a comma-separated list of addresses or `@domains` to restrict it.

**Sessions** are server-side, in a `HttpOnly`, `SameSite=Lax` cookie, and only
the SHA-256 of the token is stored — a leaked database backup does not hand
over live sessions. That is also what makes *Sign out on every device* work at
all: a self-contained JWT cannot be withdrawn before it expires.

### First run

After the first sign-in there is a three-step wizard, and every step is
skippable because the app genuinely works without any of them:

| Step | What it buys you |
|---|---|
| **Your details** | Name, date of birth, PAN, mobile — what password-protected statements are unlocked with |
| **Your mailbox** | Read-only Gmail access, so statements are found rather than downloaded by hand |
| **First import** | Opens the import wizard |

Re-run it any time from the account menu → *Run setup again*.

---

## Your data, and everybody else's

The app now serves several people from one database, which makes separation a
correctness property rather than an assumption. It is enforced in one place:

- Every table holding user data carries a `user_id`, defaulted from the
  signed-in user and covered by a PostgreSQL **row-level security** policy of
  `user_id = current_tenant()`. A query cannot read across accounts even if
  someone forgets a `WHERE` clause, and an insert naming the wrong owner is
  rejected outright.
- The tenant is bound per request by the auth middleware. Bind nothing and the
  policy matches nothing: the failure mode is an empty screen, never someone
  else's money.
- The API is **closed by default**. Everything under `/api` except health and
  the sign-in endpoints answers 401 without a session, so a new route cannot be
  born unprotected.
- Statement files, the Gmail cache and snapshots are stored under the owner,
  and deleting an account takes all of it with it.

`backend/app/db/engine.py` is the file to read first.

### Coming from the SQLite version

Your ledger is not lost. Sign in once to create your account, then:

```bash
.venv/Scripts/python backend/tools/migrate_sqlite_to_postgres.py \
    --email you@example.com --dry-run
```

Drop `--dry-run` to import. It copies every table, relocates the statement
files under your user, leaves the SQLite file and the original file tree
untouched, and is safe to run twice.

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

**Password-protected statements open automatically.** Enter your details once
during setup, or later from the account menu → **Your details** (name, date of
birth, PAN, mobile). Indian banks build statement
passwords from these — the classic format is the first four letters of your name
plus your date of birth, e.g. `jite0602`. The app generates a small, bounded set
of candidates *from those templates* and tries them against your own protected
files. This is not password cracking: it only ever runs on files you uploaded,
uses only your own details, generates dozens of format-based candidates (never a
brute-force space), and a wrong guess simply moves on. Your PII is stored
against your own account, is used only to open your own files, and never
reaches any model or network call. A working password is logged only in
redacted form (`j*******`).

**Deduplication is content-based, at three layers.** The same file added twice —
*even renamed* — is caught by its content hash and counted once. Statements that
merely overlap (a monthly and a quarterly covering the same weeks) are caught at
the transaction level. And an identical file re-uploaded in a later session
replaces its old rows via a unique-hash index rather than doubling them.

**Import straight from Gmail** (optional). Instead of downloading every
statement yourself, connect Gmail and the app finds your bank/card statement
emails, downloads the PDF attachments, and analyzes them. The security model:

- **A separate grant, read-only.** Signing in asks only for your name and email
  address. Mailbox access is a second consent you give deliberately, on
  Google's own screen, and the scope is `gmail.readonly` — it can read and
  download, and can never send, delete or modify mail.
- **You review before anything downloads.** *Scan* lists what it found; you tick
  which statements to import; only then are they pulled.
- **A token per person.** The grant is held against your account and used only
  on your behalf. **Disconnect** in Settings deletes it here *and* revokes it at
  Google.

Without setup, the manual upload path is unaffected. The whole fetch → filter →
download → parse path is covered by offline tests using a fake Gmail client, so
it's verifiable without a Google account.

### Connecting Gmail

Gmail uses the **same OAuth client as signing in** — see [Signing
in](#signing-in) above for creating it. Two extra steps are needed before the
mailbox scope will work:

1. **Enable the Gmail API.** In Google Cloud Console, search "Gmail API", open
   it and click **Enable**. Skip this and connecting fails with "Gmail API has
   not been used in project…".
2. **Add yourself as a test user.** *OAuth consent screen* (newer consoles call
   it **Google Auth Platform → Audience**) → user type **External** → under
   *Test users*, **Add users** and add your own Gmail address. Without this,
   Google blocks your own sign-in.

Then, in the app: the setup wizard's **Your mailbox** step, or **Connect Gmail**
on the import screen. Google will warn the app is *unverified* — expected, it is
your own private app, published to nobody. Choose **Advanced → Go to \<app
name\> (unsafe)** and approve the read-only access. Then **Scan for
statements**, review the list, tick what you want, and **Import & analyze**.

Check the configuration at any time with:

```bash
.venv/Scripts/python backend/tools/check_gmail_setup.py
```

**To disconnect:** Settings → Disconnect Gmail. That deletes the stored grant
and revokes it at Google; you can also remove it yourself at
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
| PostgreSQL checkpointer | Ten years of statements is slow; a crash must not discard the parsing work |

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
  db/                    PostgreSQL schema, engine and repository
  auth/                  Google sign-in, sessions, onboarding
  api/, main.py          FastAPI
backend/tools/           Synthetic fixture generator
backend/tests/           657 tests, including fault injection and isolation
frontend/src/            React UI
```

## Tests

```bash
.venv/Scripts/python -m pytest backend/tests -q
```

The suite needs a PostgreSQL it can create a database on; it builds
`financial_agent_test` under an ordinary role, uses it, and drops it. Point it
somewhere else with `FA_TEST_ADMIN_URL`, or at an existing database with
`FA_TEST_DATABASE_URL`.

**Every test runs as its own user.** That is what replaced "a fresh SQLite file
per test": each test gets a tenant of its own and the database keeps its rows
private, so the isolation guarantee is exercised on every single test rather
than in one dedicated case.

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

Your data lives in your own PostgreSQL database — the one you or your operator
run — and reaches nothing else. Account numbers are masked to the last four
digits at ingestion and never stored in full. Text is redacted again before any
model call. Where the app is shared with other people, no query of theirs can
reach a row of yours: see [Your data, and everybody
else's](#your-data-and-everybody-elses).

This tool reports facts about your own statements and states mechanical
trade-offs. It does not give personalized investment advice, and the system
prompt forbids the model from doing so. For decisions about prepaying,
investing or restructuring debt, talk to a qualified adviser who can see your
whole picture.
