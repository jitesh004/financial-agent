# Rules reference

Every hardcoded rule this app applies to a document, what it is for, where it
lives, and how to change it.

Read this before adding a bank, a card, an alert wording or a category. Almost
every rule here exists because a specific real document defeated the previous
version, and the code comments say which one — those comments are the actual
specification and are worth reading before you change a pattern.

---

## The shape of it

A document goes through five stages. Each stage has its own rules and its own
failure mode.

| # | Stage | Question it answers | Where the rules live |
|---|-------|--------------------|----------------------|
| 1 | **Find** | Is this email worth downloading? | `ingestion/gmail_source.py`, `rules/institutions.py` |
| 2 | **Open** | Can we get the text out? | `ingestion/router.py`, `extractors.py`, `passwords.py` |
| 3 | **Classify** | Statement, bureau report or holdings? | `ingestion/router.py:classify_document` |
| 4 | **Read** | What does it say? | `normalize/`, `ingestion/bureau.py`, `portfolio.py`, `txn_email.py` |
| 5 | **Check** | Does it add up, and what is it? | `reconcile/`, `categorize/`, `analytics/` |

Two packages hold the knowledge the stages share:

- **`rules/institutions.py`** — who the institutions are. 57 records, 121 name
  fragments. This is the single source for every issuer list in the app.
- **`rules/formats.py`** — the shapes documents are written in. Month names,
  payment rails, "no figure here" tokens, masked account numbers.
- **`rules/passwords.py`** — the five PDF password formats and what each needs.

---

## 1. Find: which emails matter

### Four scans, four questions

`gmail_source.SCAN_INTENTS` defines four searches. They are separate because
they want different documents over different time windows — a holdings
statement is a photograph of one date, so last quarter's is history, while a
bank statement from the same month is still money to account for.

| Intent | Looks for | Attachment required | Default window |
|--------|-----------|--------------------|----------------|
| `statement` | Bank, card, loan statement PDFs | yes | whole mailbox |
| `bureau` | CIBIL / CRIF / Experian / Equifax reports | yes | whole mailbox |
| `investment` | Broker, demat, mutual fund statements | yes | whole mailbox |
| `transactional` | One-line alerts, amount in the body | **no** | 2 months |

The `transactional` window is a *default*, not a cap. It used to clamp: asking
for a year of alerts silently got you two months, which is the app overruling a
choice the user made and saying nothing.

### Query senders vs accepted senders

Two different lists, deliberately.

- **Query** (`institutions.query_senders`) goes into the Gmail `from:` clause.
  For statements it is 14 generic mailbox words (`bank`, `noreply`,
  `estatement`…) because statements arrive from every institution that exists
  and naming them one by one would still miss the long tail. For the other
  three scans it is the issuers themselves, because alerts, bureau reports and
  holdings statements come from a knowable set — and there the generic words
  are actively harmful: a scan carrying `noreply` and `alerts` came back with
  LinkedIn, Zoom and a jobs board.
- **Accepted** (`institutions.fragments_for_scan`) is the local test after
  download. Issuer names only. A generic word here would accept every
  automated mailer in the mailbox as a bank.

Every `from:` term is space-free — a `from:` clause matches an address, and
addresses have no spaces.

### Rejection rules, in priority order

`gmail_source.REJECTION_RULES`. Each returns a *reason*, surfaced in the UI, so
an excluded email is visibly excluded-for-a-stated-cause rather than silently
missing.

| Order | Rule | Catches |
|-------|------|---------|
| 1 | `PROMOTIONAL_SUBJECTS` → `marketing` | offers, pre-approved, "now LIVE!!!" |
| 2 | `_EMOJI` → `marketing` | any emoji in the subject |
| 3 | `CERTIFICATE_SUBJECTS` → `tax certificate` | Form 16, interest certificates |
| 4 | `ADVICE_SUBJECTS` → `payment advice` | single NEFT/IMPS credit notes |
| 5 | `NOTICE_SUBJECTS` → `account notice` | unclaimed, foreclosure, lien |
| 6 | `NON_STATEMENT_SUBJECTS` → `not a statement` | OTP, KYC, AGM notices, e-voting |

Then, in order: an explicit statement subject wins → a known statement sender
wins → otherwise `no statement signal`.

Repeated exclamation marks are near-diagnostic of marketing: statement
generators never emit them. So is an emoji.

### Attachment filenames

`NON_STATEMENT_FILENAMES` drops terms & conditions, MITC, tariff sheets,
privacy notices, brochures, FAQs, welcome kits and annexures. Card issuers
attach these to the same email as the statement; each one fails to parse and
shows up as a scary-looking error.

**To add an institution:** one record in `rules/institutions.py`. Nothing else.

---

## 2. Open: getting the text out

### Format detection

`router.detect_format` reads magic bytes first, extension second. Users rename
files, and mail clients hand out `.xls` files that are really HTML — trusting
the extension is how you get "no tables found" on a perfectly good statement.

`%PDF` → PDF · `PK\x03\x04` → ZIP (xlsx/docx, disambiguated by peeking inside)
· `\xd0\xcf\x11\xe0` → legacy xls/doc.

### Passwords

Indian banks lock emailed statements with a format built from details the
customer already knows. `rules/passwords.py` holds five formats:

| Format | Needs |
|--------|-------|
| `Name(4) + DDMM` | full name, date of birth |
| `DDMMYYYY` | date of birth |
| `PAN` | PAN |
| `Mobile(10)` | mobile |
| `Card(4) + DDMM` | date of birth |

Which issuer uses which is on the `Institution` record. An issuer may add a
`password_note` — HDFC documents the name in CAPS, ICICI in lowercase — without
duplicating the format.

`MAX_CANDIDATES = 400` is a hard cap. This is format reproduction, not
cracking: a brute-force space for an 8-character password is ~10^14, and the
cap also guarantees a huge profile cannot turn this into a brute-forcer.

`profile_can_satisfy` reads the format's declared `needs`. It used to sniff
substrings out of the label, so renaming a label silently changed what the app
believed it required.

### Table extraction thresholds

| Constant | Value | Meaning |
|----------|-------|---------|
| `MIN_TABLE_COLUMNS` | 3 | narrower rows are page furniture |
| `MIN_TABLE_ROWS` | 2 | |
| `MIN_DATED_ROWS` | 5 | below this, try the expensive `stream` extraction too |
| `MIN_PARSEABLE_ROWS` | 1 | a one-row statement is still a statement |

---

## 3. Classify: which reader gets it

`router.classify_document`. **Order matters**, and the statement pipeline is
last because it is the only one with a reconciliation gate to catch its own
mistakes.

1. `looks_like_bureau_report` — a bureau is named **and** ≥2 of: credit report,
   credit information, credit score, account information, enquiry, cir, credit
   vision.
2. `looks_like_portfolio` — but `looks_like_trades` is checked **first**: a
   contract note fires every holdings signal and its numbers mean something
   else entirely. One Upstox "Global Transaction Statement" was read as
   holdings and produced a ₹2.36 **crore** position that was in fact closed at
   zero. Then an ISIN alone is proof; otherwise ≥2 soft markers.
3. Otherwise: statement.

The ISIN pattern carries two load-bearing negative lookaheads:

```python
ISIN = re.compile(r"\b(?!XX)(?![A-Z0-9]*[X*]{4})([A-Z]{2}[A-Z0-9]{9}\d)\b")
```

A masked account number has exactly an ISIN's shape — `XXXXXXXX1951` is two
letters, nine alphanumerics and a digit. Twelve months of ICICI savings
statements, eighty transactions each, were routed to the holdings reader and
filed as empty portfolios. Those transactions were never read at all.

---

## 4. Read

### 4a. Bank and card statements

**Identity comes from the letterhead only.** `metadata.letterhead()` cuts at
the first transaction row. A savings statement's body contains "HOME LOAN EMI"
on every EMI row and "CREDIT CARD PAYMENT" on every bill payment — matching
those relabels the whole account, flips its sign convention, and makes a good
statement fail reconciliation. The metadata window is the first 45 and last 15
lines.

**Institution:** most-named-wins over `INSTITUTIONS` (derived from the
registry), skipping names preceded by a landmark word (`opp`, `near`,
`behind`…) so a branch address does not name the bank.

**Account number** — `detect_account_number`. The precedence is the rule:

1. Amex's `XXXX-XXXXXX-31004` shape.
2. Labelled, **card number first**, then account number, A/C, loan account,
   folio, membership.
3. Unlabelled masked candidates.

Card-before-account is load-bearing. HDFC's Marriott statement prints both, one
line apart:

```
Credit Card No.            00361147XXXX6885
Alternate Account Number   0001015980001716889
```

With the generic label first the card was filed as `XXXX6889` — a number that
identifies something else — and all fifteen of its transaction alerts were
refused for belonging to an account that did not exist.

Never read as an account number: `customer id`, `cust id`, `ckyc`, `crn`,
`relationship no`. One customer ID spans every account the bank holds for you.
Reading it as an account number filed half of one salary account's statements
under `XXXX9341` and half under `XXXX1951`, double-counting every overlapping
month.

**Card variant** — 42 product names (Regalia, Millennia, Amazon Pay, HPCL,
Marriott Bonvoy…), longest fragment wins. ICICI is a documented exception: its
logo decodes as unmapped `(cid:NNN)` glyphs and the product name appears
nowhere in plain text, so the filename (`Retail_HPCL_NORM.pdf`) is the only
signal that survives extraction.

**Account type** — 9 patterns, most specific first: `home loan` must beat a
bare `loan`.

**Columns** — `column_map.COLUMN_ALIASES` scores each header cell against alias
sets (8–13 aliases per role) rather than keeping a per-bank template. Only
`txn_date` is required. Description deliberately is not: IDFC card statements
render a payment as `30 Sep 25   190.96 CR` and nothing else, and demanding a
description discarded those entire statements.

When there is no usable header — common when the header row was on the previous
page — `infer_roles_from_data` classifies columns by what the cells contain.

**Table selection** scores `rows×10 + mapping_confidence×20 +
best_confidence×40 + 25(has description) + density×30`. Worked examples are
skipped outright: Indian card issuers must print a "Most Important Terms and
Conditions" section that demonstrates interest with a *fictional* statement —
dates, merchants, amounts and all — and it extracts exactly like a ledger.
ICICI's HPCL card prints two.

**Direction** — on the description, in this order:

1. `_CARD_BILL_PAYMENT` → credit. This is the one narration whose meaning flips
   with the account: on the **card** it is money arriving, on the **bank**
   account funding it, money leaving. Read as a debit on both sides, every bill
   payment was counted twice.
2. `_DEBIT_WORDS` and not `_CREDIT_WORDS` → debit.
3. `_CREDIT_WORDS` → credit.

`BALANCE_MARKER_ROW` drops carried-forward and summary rows. The `b/f` and
`c/f` abbreviations **must** carry their slash: making it optional turned every
merchant starting "CF" or "BF" into a balance marker, and `CF FOODS BANGALORE`
was silently dropped. HSBC's `NET OUTSTANDING BALANCE` alone added ₹4.28 lakh
of phantom spending across 11 statements.

**Reconciliation** — `opening + credits − debits == closing`, tolerance ₹1.00.
Above that it is not a rounding artefact, it is a broken parse.

A wrong balance is worse than a missing one — a missing one makes the gate
report NOT_APPLICABLE and say so, while a wrong one makes it accuse the
transactions. Reading balances from the line *below* a header row was tried and
reverted for exactly this reason: it fixed 5 BOBCARD files and took
reconciliation failures from 5 to 27.

### 4b. Credit bureau reports

No opening balance, no running total, nothing that has to add up — so the
reconciliation gate does not apply and running it would report every report as
unreconciled forever. What a bureau report *is*: the only source that can
reveal an account the ledger has never seen.

Fields are found **by label, not by position** — that is slower than a fixed
layout and survives the layout changing, which it does. 11 field labels with
3–7 aliases each.

Two subtleties worth knowing before you touch it:

- `_BOUNDARY_ONLY` lists labels the reader does not want but must still
  *recognise*. A value runs until the next label starts, so an unlisted label
  is not a boundary — and `Account #:` swallowed `Info. as of: 09-08-2026`,
  making the last four digits of every account on the report `2026`.
- `_spaced()` matches an alias with spaces wedged inside it. The extractor lays
  glyphs out individually and preserves the gaps, so `Info. as of:` arrives as
  `I nfo. as of:`.

Score patterns are bounded to 300–900. Anything outside is a page number, a
postcode, or a truncated amount that happened to sit near the word "score".

### 4c. Holdings statements

Four layouts, tried in order — the depository layout **before** the generic
broker one, because a CAS names the brokers whose holdings it consolidates:

`cas` (CDSL/NSDL) → `cams` → `kfintech` → `broker`.

Column hints are ordered and the order is load-bearing. A CAS prints both
"Cumulative Amount" (what was put in) and "Valuation" (what it is worth now);
with a bare `amount` hint ranked above the invested ones, every position was
reported at its cost with no gain.

Header cells are matched in two forms — squeezed and spaced — because the PDF
lays each glyph out separately and the extractor preserves the gaps:
`V a lu a tio n`, `N A V`.

Identifiers are stripped of soft hyphens, zero-width spaces and non-breaking
spaces. Holdings are deduplicated by (account, ISIN, folio), so a folio that
wraps across a line in one month is a *different holding* the next.

The check: `sum(units × NAV) == printed total`.

### 4d. Transaction alerts

Statements are cut 5–15 days after the month ends, so the most recent fortnight
is always missing from a ledger built only from them. Alerts close that gap,
and every constraint below exists because of what they cost.

**Parsed by per-issuer template, never by model.** An alert is a fixed sentence
a bank's own system generated, so a regex reads it exactly or not at all — and
"not at all" is a far better outcome than a plausible guess at somebody's rent.

12 templates, most specific first: `upi-debit`, `upi-credit`, `card-spend`,
`atm-withdrawal`, `indusind-credit`, `indusind-debit`, `card-used-at` (HSBC),
`card-used-on` (ICICI), `neft-out`, `neft-in`, `generic-debit`,
`generic-credit`.

`direction` is **fixed per template**, never read from the wording. "Credited"
and "debited" appear in the same email often enough — "Rs 500 debited…
available balance credited" — that reading whichever verb comes first gets the
sign wrong, and a sign error is a two-for-one mistake in every total.

`NOT_A_TRANSACTION` blocks money that has not moved: due reminders, scheduled
payments, failed and declined transactions, OTPs.

Every alert row is marked `source='email_alert'`, kept out of the
reconciliation gate, and **superseded** the moment the real statement for that
period arrives (`SUPERSEDE_DAY_WINDOW = 3` days). The statement always wins: it
is checked, the alert is not. Without supersession, importing a statement for a
month whose alerts are already in the ledger counts every payment twice — and
the more diligent the user, the more wrong their spending becomes.

The alert is flagged, not deleted. It really did arrive, and being able to see
that the checked row replaced it is worth a column.

**To add an issuer's wording:** one `Template(...)` in `txn_email.TEMPLATES`,
placed above the generic patterns. Read the sentence off a real alert.

---

## 4b. Which month a transaction counts in

`analytics/periods.py`. The default is the row's **own calendar month**, and a
one-off is **never** moved. Only a *monthly recurring series seen at least 3
times* can drift, and the rules are:

1. **Its payday is the circular median day.** Pay landing on the 31st, 1st,
   30th and 2nd is one payday either side of a month boundary; a plain median
   gives the 16th, which is wrong about every one of them.
2. **Late arrivals go back, early ones go forward.** Payday on/after the
   **24th** and it arrives on/before the **6th** → the *previous* month's pay.
   Payday on/before the **6th** and it arrives on/after the **25th** → next
   month's, early.
3. **A series never contributes twice to one month.** Shifting can *create* the
   double count it exists to prevent — pay on 31 Aug and again on 1 Sep, and
   moving September back lands both in August and empties September. The
   occurrence nearest payday keeps the month; the other is **put back in its
   own calendar month**, moved rather than merely annotated.
4. **Two genuine payments in one month are flagged, not moved.** When no shift
   can separate them the row gets `needs_review` with the reason, rather than
   being silently moved to a month it may not belong to.

The day thresholds are named constants (`MONTH_END_ANCHOR` and friends) because
the Rules screen prints them; a number typed twice eventually disagrees with
itself.

### 4c. Selecting a period

The same module's other half, and the reason it matters that the rules above
are one implementation: **every period control in the app selects whole
accounting months**, so a period's figures are the months' figures.

`PERIOD_PRESETS` is the catalogue — all time, this month, last month, 3 / 6 /
12 months, year to date, last calendar year, this and last financial year
(April to March, `FY_START_MONTH`), and the two custom shapes. `/api/periods`
serves it **already resolved**, and the frontend renders its picker from that
rather than resolving anything itself: "the last three months" implemented on
both sides is two answers that eventually differ, and the one that matters is
the one the rows were filtered by.

A resolved `Period` is one of three shapes:

| `mode` | Selects on | Where it comes from |
|---|---|---|
| `months` | the accounting month, `start_month`..`end_month` | every preset, and a custom *month* window |
| `dates` | `txn_date`, `start`..`end` | a custom *date* window |
| `all` | nothing | all time |

`effective_month_sql()` is that selection as SQL, and both the repository's
transaction filters and the Explore query engine use it — including its
fallback to the calendar month of the date, so a row imported before accounting
months existed still lands in a month rather than in none. Filtering the bare
column would have quietly excluded every such row from every period.

Two consequences worth stating:

- **A window's reported dates are the rows' real dates.** Ask for August and
  the header reads "27 Jul → 1 Sep" if that is when August's rows fall. The
  month boundaries are the *selection*; the dates are the *coverage*.
- **`months_covered` counts months, not the calendar span.** August's rows can
  run across three calendar months, and dividing one month of figures by three
  would put every per-month average on the screen out by a factor of three.

### 4d. What a month costs

`analytics/budget.py`. Splits the window's outflow into what is committed and
what is chosen, and it is the one part of the app that answers a question about
the *future* from nothing but the past.

**A commitment** is a recurring series (§ the recurring detector) that is a
debit, active, and on a cadence between 20 and 400 days — a weekly charge is a
habit, not a commitment, and a yearly premium is one and gets normalised.
Each is classified into one of three kinds, and the distinction is the point:

| Kind | What it is | Counted in "a month costs"? |
|---|---|---|
| `debt` | EMI, loan interest | Yes |
| `spending` | rent, utilities, insurance, subscriptions | Yes |
| `saving` | SIPs and anything else in `investment` | **No** — the money is still theirs |

Counting a SIP as an expense makes a diligent saver look reckless, so it is
reported as spoken for and excluded from the cost.

**How long a commitment lasts** is only knowable for debt, and only from the
loan's own amortization: `_attach_end_date` matches the series to a
`LoanProjection` on the account first and the EMI amount second, and refuses
the match when the amounts differ by more than a quarter — one account can
carry two loans, and the wrong payoff date is worse than none. Everything else
says *until you stop it*, which is the truthful answer for a subscription.

**The variable side** is every expense-role row that no commitment accounts
for, grouped by category. Its monthly figure is the **median** of the per-month
totals, never the mean: one holiday would otherwise set the expectation for
every month after it. A category present in *every* month of the window is
flagged `every_month` — groceries recur even though no single merchant does,
and that is a different kind of thing from one big trip.

Membership comes from the series' own `transaction_ids` and from each row's
`recurring_series_id`, because either source alone has a gap. It matters: a row
counted as both a commitment and as variable spending would be counted twice.

## 5. Check

### Categorization

51 rules in `categorize/rules.py`, first match wins, matched against both the
raw and the normalized description. Order is the specification: `HDFC HOME LOAN
EMI` has to be seen as EMI before the bare `HDFC` is seen as a bank transfer.

One rule deserves calling out because it is a deliberate *omission*. Bare "EMI"
is not matched. HDFC prints the literal word as a prefix on any one-time
purchase converted to an installment plan — a hospital bill, a fuel fill-up, a
dinner. That is a *payment method*, not what the money was for, and matching it
pre-empted every more specific rule. Only the amortization shape counts:

```
EMI PRIN FOR TATA AIG GENERAL (020/036)
```

`(020/036)` is installment 20 of 36 — the one unambiguous marker of a real
schedule, as opposed to a merchant whose name starts with "Principal".

Indian payroll narrations run tokens together, so `\bSALARY\b` never matches
`PRIVATELIMI-JITESHSALNOV25//CMS3`. `SAL` anchored on a following month
abbreviation and year is specific enough not to catch "SALE".

### Transfers, settlements and thresholds

| Rule | Value | Why |
|------|-------|-----|
| Balance tolerance | ₹1.00 | statements round to paise; some publish to the rupee |
| Transfer day gap | 4 | NEFT/IMPS settle same-day; a weekend stretches it |
| Transfer amount tolerance | ₹0.01 | transfers move an exact figure |
| Reversal day gap | 3 | |
| Settlement day gap | 7 | card issuers post with more lag |
| Settlement residual | ₹500 or 2% | wallet top-up or rounding |
| Settlement candidates / legs | 12 / 5 | combinatorial bound |
| Settlement confidence floor | 0.5 | below this, review rather than apply |
| Bureau auto-link / suggest | 0.9 / 0.5 | |
| Recurring: occurrences | 3 | |
| Recurring: amount variance | 35% | utilities drift; subscriptions barely move |
| Alert supersede window | 3 days | |

Multi-leg settlement has an anti-false-positive design worth understanding
before you loosen anything: subset-sum over a whole ledger **will** find
coincidental matches, so arithmetic alone is never sufficient. A group is only
attempted when the bank leg independently names a payment rail (CRED,
DREAMPLUG, BBPS, an issuer name, a card's last four). Confidence decays fast
with group size — one leg matching one leg on an exact amount is strong
evidence; five legs summing to a sixth is what a large ledger produces on its
own.

`NOT_REPORTED_BY_BUREAUS` = savings, current, wallet, investment, unknown.
Bureaus report credit, not deposits; saying a savings account is "missing from
your credit report" would be noise dressed as a finding.

---

## The machinery itself

| Area | What is now published |
|---|---|
| **Pipeline order** | The ten stages in sequence. Order is a rule: duplicates go before transfer matching (a duplicate paired with its own original hides both), and your saved decisions are applied **last** so a decision always beats a rule. |
| **Format detection** | The three magic-byte signatures and the supported extensions. Bytes decide, not the extension. |
| **Classification order** | Bureau → holdings → statement, with the test each applies. The statement reader is last because it is the only one with a reconciliation gate. |
| **Jobs** | The four terminal states. Progress is written through as it happens, so work survives a restart. |
| **Forecast** | Committed vs discretionary, the ≥15% minimum band width, and the three confidence levels with what each requires. |
| **Loans** | Closed-form, never a model. Where a statement omits the rate it is recovered from the interest actually charged. |
| **The model** | Whether it is on, what it is used for, what it is **never** used for, the exact system prompt, and the six things stripped from a narration first. |
| **Storage** | Every clearing scope with its table count and what it can never bring back; the snapshot depth. |

## Shared vocabulary

`rules/formats.py`. These were retyped in every module that needed them, and
retyped vocabulary drifts — the bureau reader's private month map had twelve
abbreviations and no full names, so "December 2025" on a CRIF report parsed as
nothing while the same string on a bank statement parsed fine.

| Vocabulary | Contents |
|------------|----------|
| `MONTHS` | 24 spellings, abbreviations and full names |
| `RAIL_NAMES` | 20 rail and instrument codes |
| `PREFIX_RAILS` | 18 — stripped from the front of a narration |
| `SIGNATURE_RAILS` | 8 — removed from a recurring signature. Narrower on purpose: stripping "CASH" or "POS" would merge unrelated withdrawals into one series |
| `NO_FIGURE` | what a document prints where it has no figure |
| `last_four()` | the join key between a statement, an alert and a bureau line |
| `BILL_PAYMENT_MARKERS` | the 7 card-bill wordings all three readers of them share |
| `CENT` / `to_paise()` | one rounding rule for every figure the app reports |

Money is rounded **half away from zero**, the way a bank rounds — not Python's
banker's rounding, which would send 0.125 to 0.12 and fail reconciliation on
the half-paise. The totals engine and the loan calculator each had their own
identical implementation, and four more places called `.quantize` inline.

The month vocabulary had a fourth copy in `analytics/coverage.py`, built from
`calendar.month_name`. Correct, but not the same set — no "sept", which real
filenames do print, so `Statement_SEPT2025.pdf` yielded no month hint.

The bill-payment markers are deliberately a **core plus named extras**, not one
flat list. Three modules ask about a card bill for three different reasons —
the direction reader (which way does this row go), the categorizer (CC_PAYMENT,
never spending) and the settlement gate (may a multi-leg group be attempted) —
and they are not the same question. Merging them outright was tried and
reverted: "BBPS PAYMENT RECEIVED" is a card bill to the categorizer, but on a
bank account it is money arriving, and folding it into the direction reader
flipped that row's sign. So `DIRECTION_ONLY_`, `CATEGORY_ONLY_` and
`SETTLEMENT_ONLY_BILL_MARKERS` sit beside the core, each named for who needs
it.

Account-type wording is one vocabulary too. `bureau.map_account_type` defers to
`metadata.ACCOUNT_TYPE_PATTERNS` and adds only the facility words a bureau uses
that statement prose does not ("Overdraft", a bare "Vehicle"). The two lists
were independent and had already disagreed: "Wallet" on a bureau line mapped to
unknown while the same word on a letterhead mapped to WALLET.

`NO_FIGURE` is read as `None`, never zero. A bureau printing "-" for a closed
account's balance means "nothing reported"; recording that as ₹0 puts a
confident figure where there is none. A blank NAV read as zero values a holding
at nothing and drags the whole portfolio total down with it.

Date and money reading is one implementation each, in `normalize/parsers.py`:

- `parse_date(value, day_first=True, default_year=None)` — anchored. "Is this
  cell a date?" Ten shapes. Ambiguous dates default to **day-first**;
  `infer_date_order` overrides for a whole statement when one cell proves it.
- `find_date(text, ...)` — "there is a date somewhere in this sentence."
- `parse_amount(cell)` → value + explicit direction. `money()` for a plain
  Decimal, `signed_money()` where a leading minus is real.

Holdings keep their sign; a bank statement's "-500" is a debit of 500 with the
sign carried by the direction instead. That is the only real difference between
the money readers, and it is now explicit.

---

## Changing things

| To… | Edit | Then |
|-----|------|------|
| add a bank, card, broker or bureau | one `Institution(...)` in `rules/institutions.py` | nothing else |
| add a PDF password format | `rules/passwords.py`, then point issuers at it | |
| teach an alert wording | one `Template(...)` in `txn_email.TEMPLATES`, above the generics | |
| add a category rule | `categorize/rules.py`, specific before general | |
| teach a column name | `normalize/column_map.COLUMN_ALIASES` | |
| teach a card product | `metadata.CARD_VARIANTS` | |
| add a bureau field alias | `bureau._LABELS`, and `_BOUNDARY_ONLY` if it must act as a boundary | |
| add a date or money shape | `normalize/parsers.py` — once, for every reader |
| add a card-bill wording | `rules/formats.py` — the core if all three readers need it, the named extra if only one does |
| change the scan look-back options | `gmail_source.PERIOD_OPTIONS` — the UI reads it from `/api/gmail/periods` | |
| add a reporting period preset | one entry in `analytics/periods.PERIOD_PRESETS`, plus its case in `resolve_period` | nothing — `/api/periods`, the Explore schema and the picker all read that list |
| change what counts as a commitment | `analytics/budget.py` — `DEBT_CATEGORIES` for what is debt service, `MIN_CADENCE_DAYS`/`MAX_CADENCE_DAYS` for what is monthly enough to budget | |

`backend/tests/test_rules.py` guards the registries. It asserts the derived
lists still cover what the hand-written ones did, that every institution can
recognise its own printed name, that no fragment of one kind leaks into
another, and that the shared readers still handle every shape their
predecessors did. Run it before and after any change here.

---

## What the user can see

Transparency is uneven, and the gaps are worth knowing.

**Surfaced today:**

- **Why an email was refused** — every rejection carries a reason ("3 marketing,
  2 tax certificates"), shown per source on the Scanning and Choose steps.
- **Why a file will or won't open** — the password format and whether your
  profile can satisfy it, before anything is downloaded.
- **Whether a statement balances** — the reconciliation verdict in words, per
  file, on Review.
- **Where a category came from** — a chip per row (rule / learned / AI / you /
  guess), and now **which rule** fired, on hover. The categorizer had always
  computed that label and discarded it; it is stored as `category_rule` from
  this change on. Rows categorised earlier say so honestly rather than claiming
  no rule matched.
- **What is staged but not yet in the ledger** — the whole import wizard exists
  so nothing counts until you have seen it.

- **Every rule in this document** — the **Rules** tab renders the whole
  catalogue read-only from `GET /api/rules`: the 57-institution registry, the
  51 category rules in the order they run, the 12 alert templates, the email
  filters and every threshold with the reason it is that number. It needs no
  ledger, because it describes what the app *would* do.
- **What would happen to *this*** — the Rules tab's first section takes a
  narration, or a sender/subject/filename, and reports exactly what fires:
  which category rule wins, which rules also matched but lost to ordering,
  whether it reads as a card bill, what each of the four scans would decide,
  and which password format applies. It calls the same functions the import
  calls, so it cannot drift from real behaviour.

- **Why this row went the way it did** — every transaction has a `?` control
  that opens a panel answering all three questions at once: what it is (the
  category rule that fired), which way the money went (which of five signals
  decided), and what it is part of (the transfer or settlement group, every
  leg in it, the day gap and the confidence).

**Not surfaced:**

- Thresholds are *shown* but not editable. `use_llm` is the only stored app
  setting; every tolerance and window is a code constant, deliberately — see
  below.

### The five signals that decide direction

Ranked, strongest first. Recorded per row as `direction_reason`; the sentences
live in `rules/directions.py` so the screen and the reader cannot drift apart.

| # | Signal | Why it ranks there |
|---|--------|--------------------|
| 1 | The running balance moved this way | The bank's own arithmetic, not a reading of its wording. Overrides everything below — and stamps its own reason when it does, so a corrected row does not still claim the wording decided it. |
| 2 | The debit / credit column | Separate columns for money in and out; nothing to infer. |
| 2 | The cell said CR or DR | One amount column, but the cell annotates itself. |
| 3 | A type column said so | A Dr/Cr or Deposit/Withdrawal column. |
| 4 | It reads as a card bill payment | The one narration whose meaning flips with the account — money arriving on the card, money leaving the account funding it. |
| 4 | The wording says so | Weakest real signal, and why explicit outgoing words are checked before a coincidental credit word. |
| 5 | Nothing said, so money out was assumed | The assumption that, left uncorrected, once booked every salary credit as spending. Worth checking. |

### What a transfer explanation shows

Read from the transactions themselves rather than from `transfer_pairs`, which
only records the two ends of a 1:1 match — a settlement covering three cards
would otherwise report a group of two. Each panel gives the kind
(`self_transfer`, `cc_payment`, `investment`, `card_settlement`) with a
sentence on what it means, the confidence and day gap, whether *this* leg is
the counted one or the mirror, and every other leg with its date, account,
description and signed amount.

### Why the Rules screen is read-only

Editable rules would be a second source of truth for every one of them, which
is the exact fault this package was built to remove. They are code because they
are reviewed, tested against real documents, and version-controlled with the
reason each one exists. `rules/thresholds.py` **imports** the live constants
rather than repeating them, so the screen cannot show a number the app does not
use.

## Known gaps

- **IDFC Millennia** prints no card number anywhere the extractor can see it;
  identity falls back to institution + product name.
- **slice** statements (10 files) parse to 0 rows.
- The four bureaus' `_LABELS` cover CIBIL and CRIF well; Experian and Equifax
  layouts are less exercised.
- `SIGNATURE_RAILS` and `PREFIX_RAILS` differ by design, but the boundary
  between them has not been re-derived from data since the lists were merged
  into `rules/formats.py`.
