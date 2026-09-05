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

Four mechanisms enforce that.

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
written narrative, the unknown-merchant tail, and the
[Agents tab](#agents), which cannot degrade to a computed answer because
choosing what to look at is the whole of what it does.

The rule holds for the model's *categories* as well as its numbers. A model
handed a merchant string will occasionally reach for a bucket that is a claim
about a counterparty rather than about a purchase — `emi`, `loan_interest`,
`cc_payment` — and such an answer is refused unless something in the string is
actually a lender. Which brings us to the word "EMI".

### 4. "EMI" in a narration is usually an advertisement

Card issuers print the word against ordinary purchases to say the charge
*could* be split into instalments if the cardholder asked. Nothing has been
borrowed, no schedule exists, and the full price was paid:

```
22:01 EMI INFINITIRETAILLIMITEDMumbai      34,990.00
EMI CLOUDNINE PNEPPSPUNE                1,25,000.00
```

Reading that as a category was wrong twice over: it threw away the merchant —
the one thing the row does say — and it moved a hospital bill and a school fee
into the figures that report what somebody *owes*. So the token carries no
weight on its own. A row is debt only when it shows something a **lender**
writes: a mandate collected by an NBFC, a named loan product, a loan account
number, or a principal/interest split carrying its instalment counter. The
whole vocabulary is in `rules/instalments.py`, and the same file explains why
"instalment" alone is not enough either — a recurring deposit, a SIP and the
*fee* for setting up a conversion are all written with that word, and each has
a better home.

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

### Configuration is `.env`, and only `.env`

Both compose files hand the whole of `.env` to the backend container with
`env_file`, so **adding a setting is an `.env` edit and nothing else** — no
compose line to remember, and no defaults duplicated between the two files
waiting to drift apart. `backend/app/config.py` is the one place that says
what a setting is called and what it falls back to.

Two consequences worth knowing:

- **The file is what counts, not your shell.** `env_file` reads `.env`, so a
  variable you `export` in a terminal no longer reaches the container. Put it
  in `.env`, or pass it for one run with `docker compose run -e VAR=…`.
- **The whole file reaches the backend.** Including `POSTGRES_PASSWORD` and
  `FA_DB_PASSWORD`, which the app never reads. That container already holds
  the app role's own credentials, so it is a tidiness cost rather than a new
  exposure — but keep unrelated secrets out of this `.env`. The `db` and
  `caddy` containers still get only the two variables each actually needs.

A handful of settings stay named in the compose files on purpose, and they
override `.env` rather than the other way round: `FA_DATABASE_URL` and
`FA_DATA_DIR`, which describe the container and not the deployment
(`.env.example` points the first at `localhost`, which is correct for a
host-run backend and unreachable from inside a container); production's
`FA_SESSION_COOKIE_SECURE=true`, which is an invariant behind TLS rather than
a choice; and the few production variables guarded with `:?`, which fail
`docker compose up` naming what is missing instead of starting an app that
cannot sign anybody in.

The dev stack needs no `.env` at all — it comes up on a fresh clone, with
every feature that lacks a key reporting itself unconfigured. Production
refuses to start without one. (The `required: false` spelling needs Docker
Compose 2.24 or newer.)

### Or run it without Docker

You need a PostgreSQL 15 or newer server. Create the database and the
**ordinary, non-superuser role** the app connects as:

```bash
FA_DB_PASSWORD=financial_agent bash deploy/postgres-init.sh
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

Optional — for the written narrative and the unknown-merchant tail, add an
`OPENROUTER_API_KEY` to `.env`. The two models it defaults to are billed at
zero per token; see [Language model](#language-model) for what "free" does
and does not cover.

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

### Running a demo without showing anybody your ledger

**Settings → Demo** points every screen at a generated workspace: fourteen
months of statements in a **separate account**, with a salary that crosses
month ends, a card bill matched against the bank debit that paid it, an EMI
against a real loan schedule, genuine irregular spending alongside the fixed
charges, and one row that needs review. It is not a mock — those are real rows
going through the real analytics, which is what makes it worth demonstrating.

Because it is a separate account, nothing done during a demo can reach the real
ledger: the switch decides which account the app *reads*, and it never moves a
row. Turning it off leaves the workspace as it was, so the next demo picks up
where the last one finished; **Rebuild** throws it away and generates it again.
A banner sits above every screen while it is on, because the risk is not
confusing the two during a demo — it is coming back on Monday, forgetting the
switch, and concluding something about your own money from generated numbers.

### The operator's view

If `FA_ADMIN_EMAILS` names your address, an **Admin** tab appears: who has
signed up, how often they come back, how many documents they have parsed, how
many transactions are stored, which import routes and institutions are in use.

Three things about it are deliberate:

- The grant comes from the environment and nowhere else. Nothing in the
  database or the UI can award it, it is matched on the whole address rather
  than a domain, and the default is **no admin at all**.
- It answers **404** to everyone else, not 403 — whether a deployment has an
  operator's view is not a useful thing to confirm to somebody probing for it.
- It reports **counts, never amounts**. There is no bypass of row-level
  security anywhere in this app, so each account's figures are counted with
  that account bound as the tenant, through the same policy every request goes
  through. It can count rows it cannot read, and a test asserts no amount,
  description, merchant or category ever reaches the payload.

Set it in `.env`, comma-separated for more than one address:

```
FA_ADMIN_EMAILS=you@example.com
```

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
- **Position** — what *you* have checked and confirmed, aged to today. The one
  screen the documents do not produce, and the only one that can hold a loan
  no statement mentions. See below
- **Agents** — the questions you would have had to know to ask. Each one is
  handed the whole ledger and a job, decides for itself what to look at, and
  shows its working. See below
- **Budget** — what a month costs before you decide anything: which charges are
  fixed and *for how long*, which vary and by how much, and what is left
- **Spending** — category and merchant breakdowns, per-month trends, outliers,
  and *"after the salary landed, where did it go?"* traced between paydays
- **Debt** — amortization per loan, payoff dates, total interest remaining, and
  how much of the *next* EMI is interest rather than principal
- **Forecast** — committed vs discretionary cashflow with a low/expected/high
  band, runway, and the first projected shortfall month
- **Transactions** — full ledger; recategorizing one teaches the merchant
  permanently
- **Files & quality** — per-file reconciliation status and every matched transfer
- **Admin** — only for an address in `FA_ADMIN_EMAILS`: signups, visits and
  volumes across the deployment, counts only

Every one of those that is *about a period* reads one, shared, from a control
above it — see below. And **every figure opens the rows behind it**: click a
category, a merchant, a month, a commitment or a headline total and the
transactions it was summed from appear, in the same period, with their own
total. A number you cannot open is a number you have to trust.

---

## The questions this is built to answer

Not "what does the data say" — the questions people actually ask themselves:

| The question | Where it is answered |
|---|---|
| How much did I earn last month? | **Overview**, with the period set to *Last month* — and the month strip under the cashflow chart opens any month's rows |
| Which of my expenses are fixed? For how long? | **Budget → Fixed every month.** Debt carries its payoff date and the number of payments left, read from the loan's own amortization; a subscription says *until you stop it*, because that is the truth |
| What is my spending pattern? | **Spending** — categories, merchants, per-month trend, and outliers flagged against their own category |
| How much did I save? | **Overview → Net saved**, with the savings rate |
| How much went on extras? | **Budget** splits the month into committed and chosen. What is left after the commitments *is* the discretionary part |
| What is my monthly budget? | **Budget → A month costs.** Commitments that leave for good, plus the median month of everything that varies. Nobody types a target in; it is what your own statements say a month costs |
| Which expenses repeat month on month — EMI, school fees, utilities, recharges, insurance? | **Budget**, in two lists: charges that recur as a *series*, and categories that appear in *every month* even though no single merchant repeats (groceries are the usual case) |

### How "this repeats" is decided

Not by the median gap between charges. That is the obvious method and it is
wrong in three ways that all show up on real statements: a single missed month
turns a monthly charge into a "bi-monthly" one, months are not 30 days so the
tolerance has to be wide enough to also admit coincidences, and it throws away
the strongest signal there is — *the 5th of every month* is not a fact about
gaps at all.

So every candidate cadence is **fitted**. Each date is assigned to the period
it lands in — whole months for the monthly family, days for the weekly one —
and the fit is scored on four things at once: how close the dates sit to the
rhythm they claim, how tightly they cluster on one day of the month, how many
periods in the span actually hold a charge, and whether any period holds *two*,
which is proof the rhythm is wrong. The best-scoring cadence wins and its score
becomes the confidence.

Three consequences worth knowing:

- **A missed month costs coverage and nothing else.** It does not change what
  cadence the charge is on.
- **A salary paid on the last working day is monthly.** It lands on 31 May one
  year and 2 June the next, so a date on the far side of a month boundary from
  the anchor is counted as the anchor's month — the same correction that
  decides which month a payment is *reported* in.
- **A price rise keeps the series.** A clean level shift, or a steady drift
  like a loan's interest component falling every month for twenty years, is
  recognised as one charge with two levels — and the going-forward figure is
  the *current* level. Rent that went from 41,500 to 45,000 costs 45,000 next
  month, not the average of the two.

Every detected series carries the sentences the detector wrote about its own
reasoning, and the Recurring tab shows them next to the rows the series was
inferred from.

Two rules keep those answers honest:

- **A SIP is not an expense.** Money moving into an investment every month is
  as committed as an EMI and as unavailable to spend, but it is still yours.
  Counting it as spending makes a diligent saver look reckless, so commitments
  are reported as debt, fixed spending and committed saving — and only the
  first two are subtracted to reach what a month costs.
- **"Typical" is the median month, never the mean.** One holiday, one hospital
  bill or one wedding would otherwise set the expectation for every month
  after it.

---

## Position

Every other screen is derived from a document, and is therefore only as
complete as the documents that have been imported. Position is the other half:
the place where you say *this is my reality, I have been through it*. It exists
because there are facts no statement carries — a loan serviced from an account
nobody uploaded, a tenure agreed on the phone, a card whose PDF is lost.

The obvious objection to letting anyone type "outstanding: 42,00,000" is that
it is true for exactly one day. That is a property of the number, not a reason
to refuse it, and two things answer it:

**An attested figure ages.** Nothing here is displayed as typed. A loan is
rolled forward from the day you confirmed it through the same closed-form
amortization the Debt tab uses, so a balance signed off in January reads three
instalments lighter in April — and the row shows both, with a sentence saying
which is which:

```
Home loan          ₹41,26,891     221 left   paid off Jan 2045
                   from ₹42,00,000 · 3 EMIs on
                   as you reviewed it on 5 Jan 2026, rolled forward 3 instalment(s)
```

**An attested figure is checkable.** Where the row is mapped to a statement,
the rolled-forward number is compared against what the bank actually says and
the difference is reported — never resolved silently in either direction. A
statement is checked and an attestation is not, so the statement is usually
right; but the whole reason this table exists is that statements are sometimes
absent or months behind.

### A card is the deliberate exception

A card balance does not amortize — it is whatever was spent minus whatever was
paid — so projecting one would be inventing a liability, which is the single
number on this screen that must not exist. What a card *does* have is a cycle,
and that is arithmetic: the next statement date and the next due date are
computed, and the balance is marked stale the moment a statement has been
generated since you last looked.

### Three of four terms are enough

A loan has four numbers — balance, EMI, rate, remaining term — and any three
determine the fourth. The rate is the one nobody remembers, so give the other
three and it is recovered by bisection and labelled as *worked out* rather than
confirmed. Give all four and disagree with yourself, and it says so at the
moment you type it rather than letting a payoff date come out four years wrong.

### What nothing accounts for

The most important thing on the screen is the list of credit accounts your
position does *not* cover. A bureau report names every account a lender has
reported; anything open there and unmapped here means every total on this
screen — and every answer an agent gives — is short by whatever it holds.
Nothing else in this app can tell you that.

### A blank is never a zero

An account whose balance nobody has recorded is not an account holding nothing.
Totals show an em dash where a figure is genuinely unknown and say how many
rows are still blank, because "assets: ₹0" from a position with one empty field
in it is a worse answer than no answer.

### Reviews are a record, not a state

*"No one can deny it, because I reviewed it myself"* only holds if the review
is dated and kept. Signing off freezes the whole position as a snapshot, so
*what was I carrying in September?* is answerable in December — and so a later
roll-forward can be audited against it when the next statement finally arrives
and disagrees.

Nothing needs typing from scratch: **Draft it from what I have imported** fills
in every figure the statements and the bureau already carry, dated to when each
is actually true rather than to today. Your job is to correct what is wrong,
which is a five-minute pass. Every field is editable in place, rows can be
added and removed, each row maps to a statement and to a bureau line, and every
column sorts — *which card is nearest its limit*, *what is due first*, *which
loan has longest to run* are each one click.

---

## Agents

Every tab above answers a question somebody already knew to ask. An agent is
for the ones they cannot phrase — *am I actually going to be short in March?*,
*which of these subscriptions is quietly the most expensive?*, *what have I
already spent this year that counts against 80C?* — and it works by being
handed the ledger and a job rather than an answer to narrate.

Every agent opens with the [Position](#position) — it outranks the statements,
because it is what the user confirmed themselves and it is the only source that
can carry a debt no document mentions.

| Agent | The question it answers |
|---|---|
| **Debt Strategist** | What is my debt actually costing me, and what would change it? Prices every loan out to its last instalment, works out how much of the *next* EMI is interest rather than principal, and simulates the same lump sum against each loan so the comparison is arithmetic rather than a rule of thumb |
| **Subscription & Leak Auditor** | What is quietly draining money every month? Price rises nobody was told about, two services doing one job, annual renewals about to land, and charges that look abandoned rather than cancelled — ranked by *annual* cost, because that is the figure that decides anything |
| **Cashflow Sentinel** | Am I going to be short, and exactly when? A dated, day-by-day projection: which date is lowest, and which charges in the days before it put it there. A month that balances can still be short on the 4th |
| **Tax Utilisation** | What have I already spent that counts, and what is unused? Adds up qualifying spending under 80C, 80D, 24(b), 80CCD(1B), 80E and 80G *from the ledger* — including the home-loan principal and interest split, which is the deduction most often missed |
| **Emergency Fund & Resilience** | How long could I last if the income stopped? Two burn rates, because "six months of expenses" means nothing without knowing which expenses: what a month costs today, and what still has to be paid in the month somebody loses their job |
| **Bill Shock Forecaster** | What large bill is about to land that I have forgotten? The annual and quarterly charges that are invisible eleven months of the year — and, the part that matters, whether the month each falls in can absorb it. Two annual bills in one month is the case that hurts, because each is affordable alone |
| **Lifestyle Creep Detector** | What am I spending more on than I used to, without noticing? Separates *more often* (the count rose — a habit changed) from *more each time* (prices did) from *one expensive month*, which is not creep at all. Checks income over the same stretch before calling anything creep |
| **Credit Health** | What is actually holding my score back? Utilisation per card rather than overall — one maxed card among four drags a file that looks calm — plus any days-past-due, the file's age, and anything reported that should not be. Leads with the single biggest drag rather than listing four evenly |
| **Anomaly Watch** | Is anything here not mine, or not what I think? Large *for its own category* (a big flight is ordinary, a big coffee is not), billed twice, one appearance at a large amount, or a small test charge followed by a large one — which is the only shape that actually indicates a stolen card. Never asserts fraud |
| **Fee & Waste Auditor** | What am I paying purely for the privilege? Late fees, ATM and forex charges, annual fees, bounce charges — and interest on a revolved card balance, which is usually the largest item and the one nobody files mentally as a fee. Says what would have avoided each, with the figure |
| **Income Stability** | How reliable is what comes in, really? The spread rather than the total: typical against lowest month, how many sources and what share the largest is, whether pay lands on a date that wanders, and whether income has ever fallen below the committed outflow |
| **Ledger Trust** | How much of this app's numbers should I believe? Audits the data, not the money — missing months, files that failed to read, live bureau accounts nothing covers — and then says *which figures elsewhere are affected and by how much*, which is the part a list of statistics cannot give |

### How an agent works, and what it may not do

It is a loop, not a prompt. The model is given the job and a **read-only
toolbelt** — fourteen whitelisted computations over the user's own rows — and
each turn it either asks for tools or gives its answer. The tools are executed
here, the results go back, and it goes again until it answers or its step
budget runs out.

Three rules make that trustworthy:

- **Numbers come from tools, never from the model.** Every figure is computed
  in `Decimal` over the reconciled ledger and handed back exact. The model
  chooses what to look at and what it means; it does not do the arithmetic.
  This is the same rule as [§3 above](#3-the-llm-never-does-arithmetic), applied
  to a loop instead of a single call.
- **Every run keeps its working.** The full transcript — each tool call and
  each result — is stored with the answer, and the screen shows it under *How
  it got there*. Any number in a finding can be traced to the call that
  produced it.
- **Nothing can be written.** There is no tool that writes, no tool that takes
  SQL, and no tool that reaches outside the tenant. `ledger_query` goes through
  the same closed registry of dimensions and measures the Explore tab uses,
  which is why it is safe to let a model describe a query.

The advice boundary is unchanged from the narrative: an agent may say *"your
EMIs are 43% of take-home, which is above the 40% lenders generally treat as
stretched"* and may lay out the mechanics of an option in full. It may not tell
anybody what to buy or sell, what to prioritise with their money, or what a
market will do.

### Every figure is checked against a tool result

The rest of this app enforces "no model ever produces a figure" by
construction. An agent breaks that arrangement — it chooses what to compute
and then writes prose around the results — so the guarantee is restored
mechanically instead.

Every number any tool returned this run is collected; every number in the
finished answer is extracted, in whatever form it was written (`41,24,762`,
`41.2 lakh`, `4124761.64` all resolve to the same figure); and any
**money-scale** figure with no match is reported as unverified, on the screen
and in the run's history.

Three limits, each deliberate. Only money is checked — a model is entitled to
work out "43% of take-home" from two figures it was given, and flagging that
would flag every correct derivation there is. A match is approximate, wide
enough for rounding to two significant figures and narrow enough that a
genuinely different number does not pass. And **nothing is deleted**: silently
editing the prose would leave a sentence that reads as if it had been checked.

### Fitting a small model

Gemini's free tier meters **input tokens per minute** — 16,000, shared across
every call in the same minute. Measured against that, this loop's original
settings cost **91,250 input tokens for one ten-step run**: six minutes of
ceiling spent in a burst, which in practice is a cascade of 429s and a run
that never finishes.

So a run has a budget, chosen per model:

| | steps | one result | transcript | tools offered | brief |
|---|---|---|---|---|---|
| **compact** | 5 | 1,800 chars | 6,000 chars | 6 | the agent's short focus |
| **full** | 10 | 8,000 chars | 40,000 chars | 12 | the agent's full brief |

A whole compact run now costs **under 7,000 input tokens** — one minute's
budget, once, with room to spare. `FA_AGENT_PROFILE` forces either; left on
`auto` the model's own name decides, and an unknown name gets compact because
compact still answers on a large model while full does not answer at all on a
small one.

The short brief is **written, not truncated**. Each agent says its job twice:
once at length, once in the fewest lines that still name what to look at and
what not to conclude. A prompt cut off mid-sentence produces reasoning cut off
mid-thought, which is the failure this avoids rather than a cheaper version of
it. And the screen says which budget is in force, because a compact run and a
broken run look identical from outside.

### Runs are kept, and compared

*"Your EMIs are 43% of take-home"* is a fact any screen can show. *"They were
47% when this last ran in March"* is not, and it is the thing somebody
actually wants to know — so each run is stored and read back against the one
before it. Metrics that moved, findings that are new, findings that have gone.
That comparison is the reason to re-run an agent at all, and it is why an
agent run survives re-processing the statements: it is in the same clear tier
as the merchant cache, not with the derived data.

Agents are the one feature here that cannot degrade to a computed answer —
choosing what to look at next is the whole of what they do — so with no model
configured the screen says so rather than offering a button that cannot work.

---

## Periods, and why "August" is not "the rows dated in August"

One control sits above the Overview, Months, Spending, Recurring, Ledger and
Review, and every figure under it is for the period it names: **This month**,
**Last month**, **3 / 6 / 12 months**, **year to date**, **last calendar
year**, **this or last financial year** (April–March), or a window you draw
yourself — in whole months, or in exact dates.

The presets do not select by date. They select **whole accounting months**,
which is the month the ledger *counts* each row in:

> Pay lands on the last working day. In one month that is the 31st; two months
> later the 31st is a Sunday and it arrives on the 1st of the next. Count by
> date and August holds two salaries and July holds none — the savings rate for
> both months is wrong, and so is every average computed from them.

So asking for August gets the salary that arrived on **1 September** if that is
August's pay, and excludes the one that arrived on **1 August** because that is
July's. The Months tab has always worked this way; the period control is that
rule, applied everywhere, from one implementation
(`backend/app/analytics/periods.py`). Which month a row is counted in is
decided once at import time and shown on the row itself wherever it differs
from the date — see [§4b of the rules](docs/RULES.md#4b-which-month-a-transaction-counts-in).

A **custom window** offers both readings, and says which is which: *Months*
keeps the accounting behaviour above, *Exact dates* is the literal one — the
day a transaction happened, whatever month it is booked in.

Three things deliberately do **not** move with the period, and say so on
screen: account balances (as-of the latest statement), the cashflow forecast
(from today, forwards), and the written narrative — prose is generated once per
import, and re-titling last quarter's summary as if it described this month
would be exactly the kind of plausible-and-wrong output the rest of this app
exists to prevent.

---

## Protected PDFs, deduplication, and Gmail import

**Password-protected statements open automatically.** Enter your details once
during setup, or later from the account menu → **Your details** (name, date of
birth, PAN, mobile). Indian banks build statement
passwords from these — the classic format is the first four letters of your name
plus your date of birth, e.g. `pank1407`. The app generates a small, bounded set
of candidates *from those templates* and tries them against your own protected
files. This is not password cracking: it only ever runs on files you uploaded,
uses only your own details, generates dozens of format-based candidates (never a
brute-force space), and a wrong guess simply moves on. Your PII is stored
against your own account, is used only to open your own files, and never
reaches any model or network call. A working password is logged only in
redacted form (`p*******`).

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

## Language model

Three things use a model, and none of them touches a number:

| Tier | What it does | Volume |
| --- | --- | --- |
| `fast` | Categorises merchants that rules and the learned cache did not recognise, forty to a call; reads an unfamiliar statement letterhead for its issuer and account type | Many calls, one per batch, answers cached forever |
| `strong` | Writes the narrative from the already-computed brief | One call per analysis |
| `strong` | Runs an **agent**: a tool-calling loop over the ledger | One call per turn (5 on the compact budget, 10 on the full one), only when you press Run |

The default provider is **OpenRouter**, on models billed at zero per token:

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL_FAST=google/gemma-4-26b-a4b-it:free
OPENROUTER_MODEL_STRONG=z-ai/glm-5.2:free
```

**"Free" is not "unlimited".** OpenRouter charges nothing per token on a
`:free` model, then rate limits it per *request*: 20 a minute, and 50 a day
until the account has bought $10 of credit, after which 1000 a day. So the
scarce resource is calls, not tokens — which is why the categoriser sends
forty merchants at a time and why every answer it gets is written to the
merchant cache. A first import of several years of statements can still walk
into the ceiling; a `429` is waited out and retried (up to
`LLM_RATE_LIMIT_RETRIES`, never longer than `LLM_RATE_LIMIT_MAX_WAIT` per
wait), and merchants that outlast that are left uncategorized for the next
run rather than guessed at.

Why these two models. The fast tier wants latency and a reliable
`response_format: json_object` — Gemma 4 26B A4B is a mixture-of-experts that
activates under 4B parameters per token and honours it. The strong tier wants
prose and a context window that fits the whole brief — GLM 5.2 is a reasoning
model with native structured outputs. Both are swappable; the current free
catalogue is at
[openrouter.ai/models?max_price=0](https://openrouter.ai/models?max_price=0),
and `OPENROUTER_JSON_MODE=false` covers a model that rejects
`response_format`.

`OPENROUTER_REASONING_EFFORT` defaults to `low`. Reasoning tokens come out of
the same `max_tokens` budget as the answer, so a model left to think freely
about a forty-item classification can spend the budget and return an empty
`content` — which reads downstream as "0 from the model" on a provider that
was working. Raise it to `medium` or `high` if the narrative reads thin.

### Pointing it somewhere else

`OPENROUTER_BASE_URL` is the whole switch. Anything that speaks the OpenAI
`/chat/completions` shape works without a code change — including Gemini,
which serves one:

```env
OPENROUTER_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
OPENROUTER_API_KEY=<your Gemini key>
OPENROUTER_MODEL_FAST=gemini-2.5-flash
OPENROUTER_MODEL_STRONG=gemini-2.5-pro
OPENROUTER_REASONING_EFFORT=
```

Clear `OPENROUTER_REASONING_EFFORT` on any non-OpenRouter endpoint: the
thinking budget is sent as OpenRouter spells it (`reasoning: {effort}`),
which other layers — Gemini's included, where it is `reasoning_effort` — do
not read.

`LLM_PROVIDER` itself only accepts `openrouter` or `azure`. Anything else,
including the `gemini` this used to take, logs a warning at the first call and
calls no model — it is not a silent no-op.

Azure OpenAI is the alternative (`LLM_PROVIDER=azure`), and **no provider at
all is a supported configuration**: rules still categorise, analytics still
compute, and the narrative falls back to a summary assembled from the computed
figures.

Text is redacted on the way out regardless of provider — account numbers, PAN,
email, phone, addresses, and the holder names the app already knows. See
`backend/app/llm/client.py`.

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
  analytics/             Cashflow, recurring, loans, forecast, position
  agents/                Read-only toolbelt, agent catalogue, the tool loop,
                         and the check that every reported figure came from it
  graph/                 LangGraph state, nodes, assembly
  llm/                   Anthropic client (with redaction) + narrative
  db/                    PostgreSQL schema, engine and repository
  auth/                  Google sign-in, sessions, onboarding
  api/, main.py          FastAPI
backend/tools/           Synthetic fixture generator
backend/tests/           1000 tests, including fault injection and isolation
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
