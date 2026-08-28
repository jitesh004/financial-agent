# Financial Agent — Accounting Model, Period Engine, Customization & AI

## Context

The app sums money in and money out with no coherent accounting model. Verified in code:

1. **Someone else paying your card is booked as your income.** An unmatched card-payment
   credit falls through to `fallback_category` ([rules.py:408](backend/app/categorize/rules.py:408)),
   which returns `OTHER_INCOME` for *any* credit, and `OTHER_INCOME` ∈ `INCOME_CATEGORIES`.
2. **Split and multi-card payments can never match.** `AMOUNT_TOLERANCE` is declared at
   [transfers.py:37](backend/app/reconcile/transfers.py:37) and never used — matching is exact
   `Decimal` equality, strictly 1:1, cross-account, within 4 days, ignoring description entirely.
3. **Figures change after a server restart.** `is_mirror_leg` has no DB column, so
   `_rebuild_from_persisted_data` ([main.py:408](backend/app/main.py:408)) recomputes every
   aggregate with all mirror flags reset to `False`.

Underneath: **the enrichment pipeline exists in three copy-pasted versions**
([graph/nodes.py](backend/app/graph/nodes.py), [gmail_routes.py:570](backend/app/api/gmail_routes.py:570),
[files_routes.py:183](backend/app/api/files_routes.py:183)) which have drifted — the Gmail path
accepts `use_llm` and never reads it. And **there is no data lifecycle**: one unlabelled Reset
button that also `rmtree`s every uploaded file, `reanalyze` that wipes the DB *before* parsing
succeeds, run state only in memory, and a dead `analysis_runs` table.

---

## Governing principle

**A credit card is a liability, not a wallet.**

| Event | Treatment |
|---|---|
| Card purchase | Expense, on **transaction date** |
| Card bill paid from my bank | **Transfer** — both legs excluded |
| "Payment received" on a card statement | Never income — it settles a liability |
| An expense that was never mine | **Claim** against a counterparty (see 2c) |

**Net savings is identical whichever way an ambiguous inbound is classified.** Only the
income/expense split moves. This work is about the gross figures being honest.

### The safeguard that makes inference safe

An unmatched settlement means "someone else paid" **only if I hold the funding account's
statement for that period**. If August's bank statement is missing, every August card payment
looks unmatched and spend would collapse. Settlement inference is **gated on
`analytics/coverage.py`**: no `parsed` statement covering the date → `unknown_funding`, sent
to review, never silently reclassified.

---

## Workstream 0 — Workflow & data lifecycle

*(First: the accounting refactor is risky, and these are the guarantees that make it safe.)*

### 0a. Data tiers, by cost of reacquisition

| Tier | Data | Cost to regenerate | Clearable |
|---|---|---|---|
| 0 | Profile, passwords, overrides, **claims & splits**, confirmed settlement groups, custom categories | **Impossible** — human input | Factory reset only, typed confirmation |
| 1 | AI inferences | **Real money** | Dedicated action only, never a side effect |
| 2 | Statement files (Gmail cache **and manual uploads**), `source_files` | Network, Gmail quota, or *irreplaceable* if the user no longer has the file | Deliberate action |
| 3 | Parsed transactions, statements, accounts | CPU only | Freely |
| 4 | Dashboard aggregates, recurring, forecasts | Seconds | Automatically |

### 0b. Graduated actions replacing the Reset button

Moves out of the header into a **Data & Workflow** area; each states what it destroys *and
preserves*, with live counts:

| Action | Clears | Preserves |
|---|---|---|
| Refresh dashboard | Tier 4 | Everything |
| Re-analyze | Tier 4 + recurring + settlements | Transactions, files, AI, decisions |
| Re-parse statements | Tier 3–4 | Files, AI cache, decisions, profile |
| Clear downloaded files | Tier 2–4 | AI cache, decisions, profile |
| Clear AI inferences | Tier 1 | Everything else — **warns this costs money to rebuild** |
| Clear my decisions | Tier 0 overrides/claims | Everything else |
| Factory reset | Everything | Nothing — typed confirmation |

> *"This deletes 2,499 transactions and 19 accounts. It keeps 169 statement files,
> 340 AI inferences, 27 decisions and 4 open claims."*

### 0c. No data loss, ever
- **Snapshot before every destructive action** — SQLite is one file; copy it, timestamped,
  keep N deep, offer Restore. Makes the guarantee literal for ~30 lines.
- **Never destroy before success.** `reanalyze` calls `reset()` up front
  ([main.py:474](backend/app/main.py:474)); rebuild into staging and swap on success.
- **Statement files become durable.** Today uploads land in per-run `data/uploads/{run_id}/`
  and `POST /api/reset` `rmtree`s them ([main.py:569](backend/app/main.py:569)) — an
  Amex PDF the user no longer has is gone forever. Move to a flat content-addressed
  `data/statements/` store alongside the Gmail cache, never touched below tier 2.
- **Persist run state** in the dead `analysis_runs` table, so the dashboard survives restart
  intact rather than going through the lossy rebuild path.
- **Persist job progress** so an interrupted scan/download resumes.

### 0d. Stages, derived — never a stored pointer

`GET /api/workflow` returns readiness computed from persisted data:
`profile · sources · collect (N covered, M missing) · parse (N ok, M failed, K locked) ·
review (N pending) · analyze`.

Every stage always navigable, back and forth; each shows what blocks the next. A *stored*
current-step pointer goes stale and creates the exact data-loss bugs we're removing, so it is
derived per request. Replaces `hasData` gating the whole app ([App.jsx:86](frontend/src/App.jsx:86)).

### 0e. AI inference cache — tier 1

```sql
CREATE TABLE ai_inferences (
    cache_key   TEXT PRIMARY KEY,   -- sha256(kind|input_hash)
    kind TEXT, input_hash TEXT, result_json TEXT,
    provider TEXT, model TEXT,      -- metadata, NOT part of the key
    created_at TEXT, hit_count INTEGER NOT NULL DEFAULT 0
);
```

Keyed on content, not provider — switching Gemini→Azure must not re-incur cost for
provider-independent answers ("which bank issued this?"). `merchant_categories` already
survives `reset()` and joins this tier. UI shows inferences cached vs. calls made this run.

### 0f. Manual statements (Amex) as first-class
Uploads already register in `source_files` via `_save_file_registry(state, source="upload")`
([main.py:247](backend/app/main.py:247)), and `idx_source_files_hash` already dedupes by
content — so re-uploading the same file on a later cycle is already safe. Remaining gaps:

- Durable storage (0c above) so a manual file is never destroyed.
- Manual-only accounts (Amex) appear in the **Coverage grid** like any other.
- **Per-account source awareness:** a red cell on an account that has never had a Gmail file
  offers *"Upload for this month"* instead of *"Fetch from Gmail"*. Derived from whether any
  of that account's `source_files` rows have `source='gmail'`.
- Gmail sync never disturbs manual files or their accounts.

---

## Workstream 1 — Foundations

### 1a. Unify the three pipelines
New `backend/app/pipeline/enrich.py`:

```python
def enrich_ledger(db, transactions, accounts, *, use_llm=False) -> EnrichmentResult
```

Canonical sequence: dedupe → settlement matching → rules → merchant cache → LLM → fallback →
**splits & claims** → **user overrides** → period attribution → recurring → analyze. All three
entry points become thin wrappers. Also fixes `use_llm` being dead on the Gmail path.

### 1b. Persist what analytics depends on
Migration adding `transactions.is_mirror_leg`, plus `flow_role`, `accounting_month`,
`needs_review`, `review_reason`. Follows the additive-`ALTER` pattern in `Database._migrate`
([database.py:243](backend/app/db/database.py:243)); update `_TXN_COLUMNS` and
`_row_to_transaction` in [repository.py](backend/app/db/repository.py).

### 1c. The override layer — edits must survive reprocessing
Re-analysis mints fresh uuid4 ids, so id-keyed edits die. Worse, `detect_transfers` overwrites
`category` unconditionally with no `category_source` check
([transfers.py:126](backend/app/reconcile/transfers.py:126)) — it clobbers `USER` edits today.

Everything user-authored is keyed by **content fingerprint, not row id**:
`sha256(account_key | date | amount | direction | desc_hash)`, with the components stored
alongside for self-repair (no match → retry on `(date, amount, direction, desc_hash)` and
re-key). Account identity changed twice this session via product_name splits, so this matters.

Applied as the **last** step of `enrich_ledger`, so nothing can overwrite a user decision.

---

## Workstream 2 — The accounting model

### 2a. `FlowRole` — one explicit role per rupee
Replaces the tangle of `is_spend` / `INCOME_CATEGORIES` / `is_mirror_leg` / `NON_SPEND_CATEGORIES`:

`INCOME · EXPENSE · TRANSFER_OUT · TRANSFER_IN · CARD_SETTLEMENT · CLAIM_SETTLEMENT ·
INVESTMENT · REFUND · EXCLUDED`

Derived from category + direction + settlement state; user-overridable. `Transaction.is_spend`
([schemas.py:267](backend/app/models/schemas.py:267)) stays as a derived compatibility property
so existing call sites keep working through the change. Gross and net expense reported side by
side so offsets are visible, not hidden.

### 2b. Settlement groups — `backend/app/reconcile/settlement.py`

The unit is a **group**, not a pair: a set of outflows from my accounts against a set of card
settlement credits, plus a residual. Covers 1:1, 1:N, N:1, N:M uniformly.

| Shape | Real case |
|---|---|
| 1:1 | Ordinary card bill from bank |
| 1:N | **CRED paying several cards in one debit** |
| N:1 | Two part-payments against one bill |
| N:M + residual | CRED multi-card, part-funded from wallet |

Replaces the write-only `transfer_pairs` table (never read anywhere) with `settlement_groups`,
which *is* read — so confirmed groups persist and you are never asked twice.

**Anti-false-positive design — the critical part.** Subset-sum over a whole ledger *will* find
coincidental sums. Arithmetic is never sufficient evidence alone:

- A multi-leg group is only *attempted* when the bank leg independently names a payment rail
  (`CRED`/`DREAMPLUG`, `BBPS`, issuer name, card last-4).
- Bounded search: ≤12 date-nearest candidates, ≤5 legs per side — a few thousand combinations
  against ~150 settlement rows.
- Confidence decays with group size; below a floor it goes to review rather than applying.
- Greedy order: exact 1:1 → 1:N → N:1 → N:M.
- Residual ≤ ₹500 or ≤2% → `wallet_credit`; larger → review.

Also activates the dead `AMOUNT_TOLERANCE`, widens `MAX_DAY_GAP` (4 → configurable ~7), and
adds description matching, which the matcher does not do at all today.

### 2c. Claims — the part that makes it actually tally

**Amount matching cannot solve this, and cash proves it.** If someone repays you in cash there
is no ledger trace anywhere, so no algorithm can find it. And when a ₹62,000 card payment
covers ₹50,000 that was theirs plus ₹12,000 that was yours, the amounts never line up.

So the primary act moves to the side you know unambiguously: **mark the purchase (or part of
it) as not yours.** Repayment becomes a separate, optionally-invisible event.

```sql
CREATE TABLE claims (
    id TEXT PRIMARY KEY,
    direction TEXT NOT NULL,          -- owed_to_me | owed_by_me
    counterparty TEXT NOT NULL,
    origin_fingerprint TEXT,          -- the transaction that created it
    amount TEXT NOT NULL, settled_amount TEXT NOT NULL DEFAULT '0',
    status TEXT NOT NULL,             -- open | partial | settled | written_off
    basis TEXT NOT NULL DEFAULT 'accrual',
    opened_on TEXT NOT NULL, closed_on TEXT, note TEXT
);

CREATE TABLE claim_settlements (
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    method TEXT NOT NULL,             -- bank_inflow | card_payment | cash | netting | write_off | external
    amount TEXT NOT NULL, settled_on TEXT NOT NULL,
    txn_fingerprint TEXT,             -- NULL for cash and write-off
    note TEXT
);

CREATE TABLE transaction_splits (
    id TEXT PRIMARY KEY, parent_fingerprint TEXT NOT NULL,
    amount TEXT NOT NULL, category TEXT, flow_role TEXT,
    claim_id TEXT REFERENCES claims(id), note TEXT
);   -- invariant: Σ split amounts == parent amount, enforced on write
```

**Edge cases this covers**, all selectable at review time:

| Case | Handling |
|---|---|
| Part of one purchase was theirs | **Split** the transaction; only that portion becomes a claim |
| Combined card payment (₹62k = ₹50k theirs + ₹12k mine) | Settlement matches the *bill*; the claim rides on the *purchase* — amounts need not agree |
| **Repaid in cash** | `method='cash'`, no ledger row required — closes the claim |
| Partial repayment | `status='partial'`, remainder stays open |
| Repayment in installments | Multiple `claim_settlements` rows against one claim |
| Repaid late, crossing months | `basis='accrual'` (default) reduces the original month; `'cash'` books it on settlement |
| They overpay / round up | Residual → income or credit toward a future claim, user picks |
| Never repaid | Claim ages; write-off converts it to a real expense or `GIFTS_DONATIONS` |
| **I owe them** (they paid for me) | Same table, `direction='owed_by_me'` |
| Mutual debts netted off | One settlement can close several claims |
| They pay my card directly, not my bank | Unmatched card settlement links to the open claim — this is Scenario 1 |
| They pay a third party on my behalf | `method='external'` |
| Recurring shared cost (rent with a flatmate) | **`split_rules`** — pattern + my share %, applied automatically, not a monthly chore |
| Employer expense reimbursement | Same mechanism, counterparty = employer |
| Merchant refund / cashback | *Not* a claim — stays `REFUND`, a contra-expense |

**The tally guarantee.** Every rupee that moved lands in exactly one bucket. Anything that
cannot be explained surfaces as an **open claim** or an **unreviewed item** — never as silent
distortion of income or expense. A **reconciliation panel** shows, for any period: total
observed inflows and outflows, how each was classified, what remains unexplained, and what is
outstanding. Plus a standing *"Owed to me / I owe"* view with ageing.

### 2d. Review queue
Defaults chosen so an ignored queue fails safe:

| Situation | Default | Rationale |
|---|---|---|
| Card settlement, no bank leg, **coverage present** | `CARD_SETTLEMENT`, propose claim link | Structurally never income |
| Card settlement, no bank leg, **coverage missing** | `CARD_SETTLEMENT` + `unknown_funding` | Don't infer from absent data |
| Bank credit resembling an open claim | **`INCOME`**, propose the link | Weak evidence — never silently erase real income |
| Multi-leg group below confidence floor | unmatched | Arithmetic alone isn't proof |

Groups are confirmed as a unit — *"₹35,000 to CRED on 14 Aug appears to pay Axis ₹10,000 +
HDFC ₹15,000 + ICICI ₹10,000"* — then remembered so reprocessing never re-asks.

---

## Workstream 3 — Period engine (`backend/app/analytics/periods.py`)

**Calendar month, by transaction date.** A 28-Aug purchase is August spend even though it
lands on the 11-Sep statement. Billing cycles keep driving `CoverageGrid`; they don't drive
reporting.

### 3a. Salary drift
Salary paid on the last working day lands 31-Jul one month and 1-Sep the next: a double in one
month, a zero in the next. **`accounting_month`** defaults to the calendar month of `txn_date`,
shifted only for members of a monthly recurring series:

- **Circular** median day-of-month (a plain median of `31, 1, 30, 2` gives ~16 — wrong).
- Anchor ≥ 24 and day ≤ 6 → previous month. Anchor ≤ 6 and day ≥ 25 → next month.
- **Collision guard:** a series can never contribute twice to one accounting month; keep the
  occurrence nearer the anchor, flag the other.
- Recurring series only — one-offs are never moved. Always visible and overridable.

### 3b. Date ranges
`analyze()` takes explicit bounds instead of inferring from min/max `txn_date`
([engine.py:153](backend/app/analytics/engine.py:153)). `GET /api/dashboard?start=&end=` and
`GET /api/months/{month}`. Presets: This month · Last month · Last 3 · YTD · Custom.
**Partial-month flagging** via `coverage.py` — a month missing statements is badged incomplete,
never drawn as a real dip.

### 3c. Fix while here
`total_invested` vs monthly `invested` use different predicates
([engine.py:167](backend/app/analytics/engine.py:167) vs [engine.py:231](backend/app/analytics/engine.py:231));
`internal_transfer_total` double-counts pass-1/3 pairs but not pass-2; `PATCH .../category`
doesn't invalidate `RunStore`, so charts stay stale until restart.

---

## Workstream 4 — Customization

Today the only ledger-write endpoint is `PATCH /api/transactions/{id}/category`.

| Capability | Change |
|---|---|
| Note on any transaction | `user_overrides.note` |
| Recategorize durably | Existing endpoint, rewired through `user_overrides` |
| Change flow role / exclude | Same endpoint |
| **Split a transaction** | `transaction_splits`, sum-invariant enforced |
| **Mark not-mine → claim** | Creates a claim; settle later by any method incl. cash |
| Custom categories | New `custom_categories` table; category becomes a validated string over (enum ∪ custom) — **wide blast radius, flagged as a risk** |
| Recurring CRUD | `GET /api/recurring`, `/{id}/transactions`; rename, cadence, add/remove members, delete |
| Bulk edit | Multi-select → one call |

**Recurring series need stable ids** — currently fresh uuid4 on every rebuild, so
`transactions.recurring_series_id` goes stale immediately and overrides are impossible. Use a
deterministic hash of `(account_key, direction, signature)`. Expose `transaction_ids`, dropped
today in `recurring_json` ([serializers.py:245](backend/app/api/serializers.py:245)), and
actually read the write-only `recurring_series` table.

---

## Workstream 5 — Frontend

Existing conventions: plain global CSS with `var(--token)`, primitives from `ui.jsx`, no router.

| Component | Purpose |
|---|---|
| `WorkflowNav.jsx` *(new)* | Stage indicator from `GET /api/workflow`; always navigable |
| `DataManager.jsx` *(new)* | Seven graduated actions, live counts, snapshots, restore |
| `MonthView.jsx` *(new)* | Range picker; inflow/outflow/net; combined card+bank ledger; completeness badge |
| `ReviewQueue.jsx` *(new)* | Ambiguities; settlement groups confirmed as a unit; claim resolution incl. **"repaid in cash"** |
| `Claims.jsx` *(new)* | Owed to me / I owe, with ageing and write-off |
| `Recurring.jsx` *(new)* | Summary rows → expand to member transactions; inline edit |
| `CoverageGrid.jsx` | **Green cells become clickable** → that account+month's transactions render below, with statement period, opening/closing balance and reconciliation status. Orange cells also show the parse error. Red cells offer *Upload* instead of *Fetch from Gmail* for manual-only accounts. |
| `TransactionsTable.jsx` | Note, flow-role, exclude, split, bulk edit; server-side search (client-side over one page today) |
| `ui.jsx` | Fix `Chip`/`Callout` silently dropping `style` — five call sites already pass it |

The Coverage drill-down needs no new backend: `GET /api/transactions` already accepts
`account_id` + `start`/`end` ([repository.py:342](backend/app/db/repository.py:342)); the
frontend simply never sends them.

---

## Workstream 6 — AI & provider configuration

### 6a. Does AI for statement identification make sense?
**Yes — as a gated fallback, never primary, and never for numbers.**

This session needed ~8 deterministic fixes for *one* mailbox (ICICI glyph letterheads, doubled
bold text, decoy card numbers, a phone number read as an account number). That won't generalise
to arbitrary users. Institution / product / account-type are bounded classification tasks with
verifiable output. Constraints:

- **Never touches amounts, dates or balances.** The deterministic parser and the reconciliation
  gate stay authoritative; AI fills *identity* fields only.
- **Only when deterministic extraction leaves a field empty** — no cost on the ~90% that work.
- **Cached in `ai_inferences`** — re-runs free and deterministic.
- **Redaction before send.** Existing `redact()` ([client.py:29](backend/app/llm/client.py:29))
  covers digits/PAN/email/phone but **not names or addresses**, and the letterhead contains
  both. Holder-name masking and address-line stripping must land before this ships. Only the
  letterhead slice is sent, never transaction rows.

Also wire the **already-complete** `categorize_with_llm`
([llm_categorizer.py:56](backend/app/categorize/llm_categorizer.py:56)) into the Gmail and
file-retry paths, where it currently never runs.

### 6b. Provider switching
New `backend/app/config.py` — one settings object replacing three ad-hoc `os.environ` reads.
New `backend/app/llm/providers.py`: a protocol (`complete`, `complete_json`, tiers
`fast`/`strong`) with **Gemini**, **Azure OpenAI** and Anthropic adapters; `client.py`
delegates. Driven entirely by `.env` as specified (`LLM_PROVIDER`, `GEMINI_*`, `AZURE_OPENAI_*`);
`.env.example` updated.

Two blockers: `get_client()` caches the API key at first construction and ignores its `model`
arg thereafter ([client.py:138](backend/app/llm/client.py:138)); `load_dotenv` only fires on
`app.main` import ([main.py:33](backend/app/main.py:33)), so direct module imports see no config.

Dependencies: add `httpx` (only transitive today) and the Gemini SDK; remove
`langchain-anthropic` / `langchain-core`, installed but never imported.

---

## On the database — recommendation: stay on SQLite

Not the bottleneck, and switching would cost more than it returns. At 2,499 transactions and
19 accounts — even at 100× — this is single-user, single-writer, read-mostly analytics well
inside SQLite's range. WAL and a 30s busy timeout are already configured.

What actually limits things is schema and architecture, all fixable in place:

- **Money stored as `TEXT`** with `CAST(... AS REAL)` for sorting → switch to **integer paise**.
  Removes both the cast and every float-rounding risk.
- **No content-based unique constraint on transactions** — dedup is entirely in-memory today.
  Add a unique index on the content fingerprint, which the override layer needs anyway.
- **`analyze()` loads the whole table into Python.** Push aggregation into SQL where it matters.

Moving to Postgres would add a server process, pooling, migration tooling and backup
complexity — and would **break the single-file snapshot** that makes the "no data loss ever"
guarantee in 0c nearly free. `repository.py` is already a clean seam if it ever outgrows this.

---

## Verification

**Unit tests** (extend `backend/tests/`, existing style — no `conftest.py`, each file
bootstraps `sys.path`, fakes injected as plain arguments à la `FakeGmailClient`):

- Your three scenarios as explicit fixtures asserting `income == 0`, `net_expense == 0`.
- **Non-matching amounts:** ₹62,000 payment covering a ₹50,000 claim + ₹12,000 of own spend.
- **Cash settlement:** claim closes with no ledger row; totals tally; net worth unaffected.
- Partial, installment, late (accrual vs cash basis), overpayment, write-off, netting, `owed_by_me`.
- Split invariant: parts must sum to the parent, rejected otherwise.
- **CRED multi-card:** one ₹35,000 debit against three cards; N:M with wallet residual; and a
  **negative test** — a coincidental subset sum with no payment-rail narration must *not* match.
- Salary drift: 31-Jul / 1-Sep → exactly one salary per accounting month; circular median
  across a year boundary.
- Coverage gate: missing statement must not infer third-party funding.
- Lifecycle: each of the seven actions clears exactly its tier; **AI inferences and manual
  statement files survive everything except their own action**; snapshot/restore round-trips.
- Overrides and claims survive a full reprocess; `detect_transfers` cannot clobber a `USER` role.
- Manual upload → appears in Coverage → survives a Gmail sync → not duplicated on re-upload.
- Redaction: holder name and address never appear in an outbound prompt.

**Live verification** (2,499 transactions, 19 accounts):
1. Record current `income` / `spend` / `net_savings`.
2. Reprocess; confirm **net savings barely moves** while income and expense both drop by the
   settlement volume — the signature of double-counting being removed.
3. Confirm the review queue surfaces the Axis/CRED cases rather than silently deciding.
4. Every month has exactly one salary; no zero-income month.
5. Click a green Coverage cell; confirm the month's records render and balances agree.
6. Kill and restart the backend mid-work; confirm nothing is lost.

**Regression:** the full suite (currently **206 passing**) stays green throughout.

---

## Sequencing & risks

0 → 1 → 2 → 3 are strictly ordered; 4, 5, 6 parallelise once 1 lands. Checkpoint after
Workstream 2 so accounting semantics can be checked against real numbers before UI is built on
them.

**Risks on the record:** large single pass through the accounting core; making `Category` a
validated string has wide blast radius; the money-to-integer-paise migration touches every
stored amount and needs its own backfill test; and multi-leg settlement matching is the one
place where a bug yields *plausible-looking wrong answers* rather than obvious failures —
hence the narration gate, confidence floor, and negative tests.

---
---

# HANDOFF

*Workstreams 0 and 1 are **DONE** and committed. Workstreams 2-6 remain.*

## Status

| Workstream | State |
|---|---|
| 0 — Workflow & data lifecycle | ✅ **Done** |
| 1 — Foundations (unify pipelines, migrations, override layer) | ✅ **Done** |
| 2 — Accounting model (FlowRole, settlement groups, claims) | ⬜ Next |
| 3 — Period engine (salary drift, date ranges) | ⬜ |
| 4 — Customization (notes, splits, custom categories, recurring CRUD) | ⬜ |
| 5 — Frontend (MonthView, ReviewQueue, Claims, DataManager, WorkflowNav) | ⬜ |
| 6 — AI & provider config (Gemini/Azure) | ⬜ |

**Tests: 233 passing** (was 206). Run: `python -m pytest backend/tests -q`
**Git:** branch `workstream-0-1-lifecycle-foundations`, three commits on top of a
green baseline. `data/` and `.env` stay untracked — they hold statements,
passwords and PII.

## What landed

### Workstream 1
- **One pipeline.** `backend/app/pipeline/enrich.py` replaces three copy-pasted
  copies of the enrichment sequence. The Gmail route took a `use_llm` flag and
  never read it; neither non-graph route consulted the learned merchant cache at
  all, so a category the user had corrected was re-guessed from scratch whenever
  a statement arrived that way. Both fixed by construction.
- **Graph collapsed** from nine post-merge nodes to one `enrich` node. The
  fan-out and reconciliation cycle above `merge_ledger` are untouched — that is
  where the graph earns its keep.
- **`transactions.is_mirror_leg` now has a column.** Without it, every dashboard
  rebuilt after a restart recomputed with the flag reset to `False`, so the same
  ledger gave different figures depending on whether the server had restarted.
- Also added: `fingerprint`, `accounting_month`, `needs_review`,
  `review_reason`, `flow_role`, `excluded`, `note`. **Workstream 2 needs no
  further migration on this table.**
- **Override layer** (`pipeline/fingerprint.py`, `pipeline/overrides.py`). User
  decisions keyed by content fingerprint, applied *last* — previously
  `detect_transfers` reassigned categories unconditionally with no
  `category_source` check and silently destroyed hand corrections. A looser key
  recovers a decision whose account identity moved, and refuses to guess between
  two identical rows.

### Workstream 0
- **Seven scoped clearing actions** replace the single Reset button, ordered by
  cost of reacquisition. `GET /api/data/inventory`,
  `POST /api/data/clear/{scope}`, `POST /api/data/restore`.
- **Snapshot before every destructive action**, pruned to 10, restore is itself
  undoable and rejects paths outside the snapshot folder.
- **`reanalyze` no longer clears the ledger before parsing succeeds.**
- **Durable statement storage** (`backend/app/storage.py`). Uploads land in a
  content-addressed store under `data/statements/`. Inventory reports
  Gmail-cached vs hand-uploaded separately — only one of those can ever be
  re-obtained.
- **`analysis_runs` is alive**, storing completed dashboards so a restart
  restores one intact rather than recomputing a version with no narrative and no
  transfer report.
- **`GET /api/workflow`** derives stage readiness from stored data on every
  request — never a stored "current step" pointer.

### Bugs found and fixed while testing
- `sqlite3`'s context manager commits but does **not** close the connection, so
  every snapshot held a lock on the file it had just written and pruning failed
  silently on Windows.
- `executescript(SCHEMA)` runs *before* `_migrate()`, and `CREATE TABLE IF NOT
  EXISTS` is a no-op on an existing table — so an index naming a newly-added
  column aborted the whole script. Indexes over migrated columns are now created
  in `_migrate`.

## Verified on the live ledger (not just fixtures)

- 2,499 real transactions → 2,499 **unique fingerprints, zero collisions**.
- A recorded decision survives a full re-enrichment and reattaches to the right
  row.
- **Income / spend / net are byte-identical to the pre-refactor baseline**
  (₹38,79,603.92 / ₹36,93,285.54 / ₹1,86,318.38) — the refactor is
  behaviour-preserving, which is correct because the accounting changes are
  Workstream 2.
- Migration on a copy of the real database: idempotent, all rows preserved, no
  FK violations.
- Backend restarted twice: the dashboard came back with the **same run_id**,
  proving it restored from storage rather than recomputing.

## Behaviour changes a user would notice

1. **The Reset button is now much safer and does less.** `POST /api/reset` still
   exists so the current frontend does not 404, but it clears only the parsed
   ledger — no longer statement files, AI inference or decisions. The UI still
   labels it "Reset", which now overstates what it does.
2. **No UI exists yet for the seven actions or the workflow view.** Those are
   `DataManager.jsx` and `WorkflowNav.jsx` in Workstream 5. The endpoints are
   live and usable via curl in the meantime.

## Next session: Workstream 2

Start at "Workstream 2 — The accounting model" above. Notes for whoever picks it up:

- The DB columns are already in place (`flow_role`, `needs_review`,
  `review_reason`). No migration needed on `transactions`.
- `enrich_ledger` is the single insertion point. Settlement matching slots in
  where `detect_transfers` is called; claims and splits go between the fallback
  categoriser and `apply_overrides` — overrides must stay last.
- The claims/splits tables from the plan are **not yet created**. Add them to
  `SCHEMA` in `database.py` and to the tier-0 list `_TIER_DECISIONS` in the same
  file, or a clearing action will wipe them.
- `pipeline/overrides.record_decision` is the pattern to follow for any new
  user-authored write.
- Re-read the anti-false-positive constraints for multi-leg settlement before
  writing the matcher. That is the one part where a bug produces
  plausible-looking wrong accounting rather than an obvious failure.

## Decisions already made with the user — do not relitigate

| Question | Decision |
|---|---|
| Reporting period basis | Calendar month, by transaction date, with explicit salary-drift handling |
| Ambiguous inbound money | Review queue with a safe default; never silently reclassify real income |
| Rollout | Single pass, checkpoint after Workstream 2 |
| Database | Stay on SQLite |
| LLM provider | Config-driven; user supplied Gemini + Azure settings, `LLM_PROVIDER=gemini` |

## Hard constraints that must carry forward

- **Gmail is OAuth read-only** (`gmail.readonly`) and never sees a password.
- **PII stays local.** Name / DOB / PAN / mobile never reach a model or network
  call. Workstream 6 dependency: the existing `redact()` does **not** strip
  names or addresses, and the statement letterhead contains both — fix before
  any AI statement-identification ships.
- **Passwords logged redacted only** (`j*******`); never returned by any API.
- **Account numbers masked to last 4** at ingestion.
- **Password derivation bounded** (`MAX_CANDIDATES = 400`), never brute force.
- **Excluded senders never downloaded or analyzed** — `bankofbaroda`, `pnbmail`,
  `punjabnationalbank`, `rbl.bank`, `loanestatement@icici`,
  `estatements@indusind.com`. The last is narrowed deliberately: a bare
  `indusind` substring also matched the user's *own* IndusInd card.

## Still open with the user (not blockers)

- ICICI shows three card products in the mailbox — **Amazon Pay, Coral, HPCL** —
  but the stated ground truth was "Amazon pay, rubyx". Either Coral was meant,
  or a Rubyx card's statements have not been fetched.
- The **HPCL card appears as three accounts** (`XXXX5005` / `XXXX0005` /
  `XXXX3006`) because the masked number printed on it changes month to month.
  One reissued card, or three?
- Parked as a deliberate non-goal: the Axis relationship-summary e-statement
  (savings `XXXX5533`) still mislabels itself as a credit card some months. Low
  value — 23 transactions over 9 months.

## Model recommendation for the remaining work

**Opus 5 for Workstreams 2–3.** These are where being wrong is expensive *and
invisible*: multi-leg settlement matching yields plausible-looking wrong
accounting rather than obvious failures, and the claims edge-case matrix has 15
interacting cases. Calibration: this session's own snapshot-locking and
migration-ordering bugs were both found only because a test asserted the
property directly.

**Sonnet 5 is reasonable for Workstreams 5 and 6b** — React components against a
settled spec, and the provider adapters, which are thin and fail loudly.

Whichever model: the checkpoint after Workstream 2 needs a **human** looking at
real numbers. Success looks like **net savings barely moving while income and
expense both drop**. No model should self-certify that.
