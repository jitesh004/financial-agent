"""A demo workspace: the whole app, on statements nobody has to explain.

Showing this app means showing somebody a complete financial history, and the
only complete one to hand is usually the presenter's own. That is a bad trade
to have to make, so this builds a second account - generated, self-consistent,
and owned by whoever asked for it - and the Demo switch points the app at it.

Why a separate ACCOUNT rather than a flag on the rows: every screen, every
query and every row-level security policy in this app already works per
account. Pointing the request's tenant at another one therefore needs no
special case anywhere, and nothing done during a demo - a recategorisation, a
correction, a Clear ledger - can reach the real data. It also means the demo
workspace is not a mock: it is real rows going through the real analytics.

The data is built to exercise the things worth demonstrating rather than to
look plausible in the abstract:

  * a salary paid on the last working day, so it lands on the 31st some months
    and slips to the 1st of the next in others - the case the accounting-month
    rule exists for, visible on the Months tab as one salary per month
  * a card bill paid from the bank account, matched as a transfer, so the
    double-count report has something in it
  * an EMI against a real loan account, so the Budget tab can say when the
    debt ends and Debt can amortise it
  * a SIP, so committed saving is separated from committed spending
  * a refund carrying the date of the purchase it reverses, billed in a later
    cycle - the out-of-cycle case
  * one genuinely ambiguous credit, so the Review queue is not empty
"""

from __future__ import annotations

import logging
import uuid
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from random import Random
from typing import Any

from .db import repository as repo
from .db.database import Database
from .db.engine import tenant_scope
from .models.schemas import (Account, AccountType, Category, Direction,
                             FlowRole, Statement, Transaction)

log = logging.getLogger(__name__)

#: How many whole months of history the demo workspace holds. Fourteen so the
#: 12-month presets have data either side of them, and so "last 3 months" and
#: "this financial year" are both answerable.
MONTHS = 14

#: Marked on every generated row, so a demo workspace is recognisable in the
#: database as generated rather than imported.
SOURCE = "demo"

#: Which everyday categories are charged to the card rather than the bank.
_ON_THE_CARD = {Category.GROCERIES, Category.DINING, Category.SHOPPING,
                Category.FUEL, Category.ENTERTAINMENT}


def _month_starts(today: date, count: int = MONTHS) -> list[date]:
    """The first of each of the last `count` months, oldest first."""
    year, month = today.year, today.month
    out: list[date] = []
    for _ in range(count):
        out.append(date(year, month, 1))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(out))


def _pay_date(first: date) -> date:
    """When pay for the month `first` begins actually lands.

    Payroll is dated the last day of the month and credited on the next
    working day, because settlement batches do not run at the weekend. That
    is what makes the demo worth demonstrating: pay for a month ending on a
    Friday lands on the 31st, and pay for one ending at the weekend lands on
    the 1st or 2nd of the NEXT month. The second case is the whole reason the
    accounting-month rule exists, and the only thing keeping one month from
    holding two salaries and the next from holding none.
    """
    day = date(first.year, first.month, monthrange(first.year, first.month)[1])
    while day.weekday() >= 5:           # Saturday, Sunday
        day += timedelta(days=1)
    return day


class _Builder:
    """Accumulates rows for one account, keeping a running balance."""

    def __init__(self, account_id: str, opening: Decimal, horizon: date,
                 liability=False):
        self.account_id = account_id
        self.balance = opening
        #: Nothing is dated past here. A real ledger stops at today, and a
        #: demo that shows next week's groceries - and a balance including
        #: pay not yet received - teaches the wrong thing about every figure
        #: on the screen.
        self.horizon = horizon
        self.liability = liability
        self.rows: list[Transaction] = []

    def add(self, when: date, description: str, amount: Decimal,
            direction: Direction, category: str, role: FlowRole,
            *, accounting_month: str | None = None, priced: bool = True,
            needs_review: bool = False, review_reason: str = "",
            merchant: str = "") -> Transaction | None:
        """Record one row, or None if it would fall after the horizon."""
        if when > self.horizon:
            return None
        if direction == Direction.CREDIT:
            self.balance += amount
        else:
            self.balance -= amount
        txn = Transaction(
            id=str(uuid.uuid4()),
            account_id=self.account_id,
            txn_date=when,
            raw_description=description,
            normalized_description=description.title(),
            merchant=merchant or description.split("/")[0].split("-")[0].title(),
            amount=amount,
            direction=direction,
            category=category,
            flow_role=role.value,
            balance_after=self.balance if priced else None,
            accounting_month=accounting_month
            or f"{when.year:04d}-{when.month:02d}",
            needs_review=needs_review,
            review_reason=review_reason,
            source=SOURCE,
            # Content-derived elsewhere; here it only has to be unique, since
            # save_transactions dedupes on it.
            fingerprint=f"demo:{uuid.uuid4()}",
        )
        self.rows.append(txn)
        return txn


def _accounts() -> dict[str, Account]:
    return {
        "bank": Account(
            institution="Meridian Bank", account_type=AccountType.SAVINGS,
            product_name="Savings", account_number_masked="XXXX4402",
            holder_name="Demo User", currency="INR"),
        "card": Account(
            institution="Northwind Card", account_type=AccountType.CREDIT_CARD,
            product_name="Everyday Rewards", account_number_masked="XXXX7731",
            holder_name="Demo User", credit_limit=Decimal("250000")),
        "loan": Account(
            institution="Meridian Bank", account_type=AccountType.HOME_LOAN,
            product_name="Home Loan", account_number_masked="XXXX9014",
            holder_name="Demo User",
            principal_outstanding=Decimal("3820000"),
            interest_rate=Decimal("8.45"),
            emi_amount=Decimal("34200")),
    }


#: Charges that really do repeat: same payee, same time of month, every month.
#: As (category, merchant, day of month, base amount, drift). `drift` is added
#: once per month so nothing is eerily identical - a demo where every month is
#: the same number teaches nothing about a trend.
#:
#: These are what the Budget tab should find and call fixed.
_FIXED: list[tuple[str, str, int, int, int]] = [
    (Category.GROCERIES, "UPI/FRESHCART/GROCERIES", 6, 7100, 65),
    (Category.TRANSPORT, "UPI/METRO RECHARGE", 3, 1200, 0),
    (Category.SUBSCRIPTIONS, "STREAMLINE MEDIA", 8, 649, 0),
    (Category.UTILITIES, "UPI/CITY POWER BILL", 17, 1860, 45),
    (Category.UTILITIES, "UPI/FIBRENET BROADBAND", 11, 999, 0),
]

#: And the long tail, as (category, merchant, smallest, largest): different
#: shops on different days for different amounts, a handful each month.
#:
#: This half is the point. The first version of this data gave every charge a
#: fixed payee and a fixed day, so the recurring detector - correctly - called
#: all fourteen of them commitments, and the Budget tab reported a person with
#: NO discretionary spending whatsoever. "What is fixed and what is not" is
#: among the questions this app exists to answer, and a demo cannot show that
#: answer working on a ledger that has only one of the two halves in it.
_OCCASIONAL: list[tuple[str, str, int, int]] = [
    (Category.GROCERIES, "UPI/CORNER STORE", 380, 1900),
    (Category.GROCERIES, "DAYBREAK PROVISIONS", 640, 2400),
    (Category.DINING, "SPICE ROUTE KITCHEN", 700, 2900),
    (Category.DINING, "UPI/CAFE ORBIT", 180, 780),
    (Category.DINING, "TANDOOR HOUSE", 520, 2200),
    (Category.DINING, "UPI/NOODLE BAR", 260, 1100),
    (Category.FUEL, "GREENFIELD FUEL STATION", 1800, 3900),
    (Category.FUEL, "HIGHWAY FUELS", 900, 3200),
    (Category.SHOPPING, "NORTHGATE RETAIL", 1100, 6400),
    (Category.SHOPPING, "UPI/LANTERN HOME STORE", 450, 3800),
    (Category.SHOPPING, "MERIDIAN BOOKSHOP", 300, 1600),
    (Category.TRANSPORT, "UPI/CITYCAB", 140, 900),
    (Category.TRANSPORT, "UPI/AIRPORT TAXI", 600, 1500),
    (Category.ENTERTAINMENT, "ORBIT CINEMAS", 350, 1400),
    (Category.ENTERTAINMENT, "UPI/BOARDWALK ARCADE", 200, 950),
    (Category.HEALTHCARE, "WELLSPRING PHARMACY", 240, 1800),
    (Category.HEALTHCARE, "DR K RAO CONSULTATION", 700, 1500),
    (Category.PERSONAL_CARE, "UPI/CLIP N STYLE", 300, 900),
    (Category.GIFTS_DONATIONS, "UPI/PETAL & STEM", 450, 2600),
    (Category.EDUCATION, "BRIGHTPATH TUITION", 1800, 3600),
]

#: How many of the tail appear in one month, and the seed that decides which.
#:
#: Fixed seed, so the same date always generates the same ledger: a demo
#: somebody has learned their way around should not rearrange itself under
#: them, and a test that pins a figure needs the figure to stay pinned.
_OCCASIONAL_PER_MONTH = (5, 9)
_SEED = 20260101


def build_rows(accounts: dict[str, str], today: date | None = None
               ) -> tuple[list[Transaction], dict[str, Decimal], list[dict]]:
    """Every demo transaction, each closing balance, and the matched pairs."""
    today = today or date.today()
    months = _month_starts(today)

    bank = _Builder(accounts["bank"], Decimal("284000"), today)
    # A card carries no running balance on most statements, which is worth
    # demonstrating: the Position block says so rather than counting it as
    # zero.
    card = _Builder(accounts["card"], Decimal("0"), today)

    salary = Decimal("186500")
    for index, first in enumerate(months):
        pay_day = _pay_date(first)
        # A raise, two thirds of the way through.
        if index == int(len(months) * 0.66):
            salary = Decimal("204000")

        # Pay for THIS month, dated on its last working day - which is what
        # makes some of these land on the 1st of the next month once a
        # weekend intervenes. The accounting month is stated because that is
        # what the period engine would conclude, and the demo should agree
        # with it rather than wait to be re-analysed.
        bank.add(pay_day, "NEFT-CR-ACME TECHNOLOGIES PVT LTD-SALARY",
                 salary, Direction.CREDIT, Category.SALARY, FlowRole.INCOME,
                 accounting_month=f"{first.year:04d}-{first.month:02d}",
                 merchant="Acme Technologies")

        bank.add(first + timedelta(days=3), "NEFT-DR-RENT-HARBOUR VIEW APTS",
                 Decimal("41500"), Direction.DEBIT, Category.RENT,
                 FlowRole.EXPENSE, merchant="Harbour View Apts")
        bank.add(first + timedelta(days=5),
                 f"MERIDIAN HOME LOAN EMI PRIN ({index + 1:03d}/240)",
                 Decimal("34200"), Direction.DEBIT, Category.EMI,
                 FlowRole.TRANSFER_OUT, merchant="Meridian Home Loan")
        bank.add(first + timedelta(days=7), "SIP-EQUINOX BLUECHIP FUND",
                 Decimal("22000"), Direction.DEBIT, Category.INVESTMENT,
                 FlowRole.INVESTMENT, merchant="Equinox Bluechip Fund")
        bank.add(first + timedelta(days=9), "LIC PREMIUM-TERM COVER",
                 Decimal("2870"), Direction.DEBIT, Category.INSURANCE,
                 FlowRole.EXPENSE, merchant="Term Cover")

        days_in_month = monthrange(first.year, first.month)[1]

        def spend(day: int, merchant: str, amount: Decimal, category: str):
            """One everyday charge, on the card if that is where it would go."""
            # Kept inside the month whatever its length.
            when = first + timedelta(days=min(day - 1, days_in_month - 1))
            target = card if category in _ON_THE_CARD else bank
            target.add(when, merchant, amount, Direction.DEBIT, category,
                       FlowRole.EXPENSE, priced=target is bank)

        for category, merchant, day, base, drift in _FIXED:
            spend(day, merchant, Decimal(base + drift * index), category)

        # The tail. Seeded per month rather than once for the whole run, so
        # adding a month at one end does not rewrite every other month.
        dice = Random(_SEED + index)
        for category, merchant, low, high in dice.sample(
                _OCCASIONAL, dice.randint(*_OCCASIONAL_PER_MONTH)):
            spend(dice.randint(1, days_in_month), merchant,
                  Decimal(dice.randrange(low, high, 10)), category)

    # ---- the card bill, paid from the bank: one movement, two rows --------
    #
    # Both legs stay in the ledger because both really happened; the transfer
    # detector marks one as the mirror so cashflow counts it once. This is the
    # single most important thing to be able to show, because it is the
    # difference between this app's spending figure and a naive sum.
    pairs = []
    for first in months[:-1]:
        bill = Decimal("0")
        for row in card.rows:
            if row.txn_date.year == first.year and row.txn_date.month == first.month:
                bill += row.amount
        if bill <= 0:
            continue
        when = date(first.year, first.month,
                    monthrange(first.year, first.month)[1]) + timedelta(days=14)
        if when > today:
            # The bill for this cycle is not due yet, so neither leg exists.
            continue
        pair_id = str(uuid.uuid4())
        out = bank.add(when, "NEFT-DR-NORTHWIND CARD BILL PAYMENT", bill,
                       Direction.DEBIT, Category.CC_PAYMENT,
                       FlowRole.TRANSFER_OUT, merchant="Northwind Card")
        back = card.add(when, "PAYMENT RECEIVED - THANK YOU", bill,
                        Direction.CREDIT, Category.CC_PAYMENT,
                        FlowRole.CARD_SETTLEMENT, priced=False,
                        merchant="Northwind Card")
        for leg, mirror in ((out, False), (back, True)):
            leg.is_internal_transfer = True
            leg.is_mirror_leg = mirror
            leg.transfer_pair_id = pair_id
        pairs.append({
            "pair_id": pair_id, "amount": bill, "kind": "card_settlement",
            "day_gap": 0, "confidence": 1.0,
            "debit_txn_id": out.id, "credit_txn_id": back.id,
        })

    # ---- the awkward rows, one of each ----------------------------------
    latest = months[-1]
    previous = months[-2]

    # A refund carrying the date of the purchase it reverses, billed in a
    # later cycle. Real money, and not the older month's.
    card.add(previous + timedelta(days=2),
             "REFUND-NORTHGATE RETAIL ORDER CANCELLED", Decimal("4260"),
             Direction.CREDIT, Category.REFUND, FlowRole.REFUND,
             accounting_month=f"{latest.year:04d}-{latest.month:02d}",
             priced=False, merchant="Northgate Retail")

    # Money in that could be income or could be a repayment, with no way to
    # tell from the narration - so it is flagged rather than guessed at.
    # Dated a couple of days back rather than at a fixed day of the month, so
    # it is always inside the window whenever the demo is generated.
    bank.add(max(latest, today - timedelta(days=2)), "UPI/R MENON/SETTLEMENT",
             Decimal("18500"), Direction.CREDIT, Category.OTHER_INCOME,
             FlowRole.INCOME, needs_review=True, merchant="R Menon",
             review_reason="Could be income or somebody repaying you; "
                           "no statement covering the other side is loaded.")

    # One quarter's bonus, so income is not a flat line.
    bank.add(months[len(months) // 2] + timedelta(days=20),
             "NEFT-CR-ACME TECHNOLOGIES-PERFORMANCE BONUS",
             Decimal("125000"), Direction.CREDIT, Category.OTHER_INCOME,
             FlowRole.INCOME, merchant="Acme Technologies")

    rows = bank.rows + card.rows
    return rows, {"bank": bank.balance, "card": -Decimal("18400"),
                  "loan": Decimal("-3820000")}, pairs


def _statements(accounts: dict[str, str], rows: list[Transaction],
                today: date | None = None) -> list[tuple[Statement, str]]:
    """One statement per account per month, so Files and Coverage are populated."""
    today = today or date.today()
    out: list[tuple[Statement, str]] = []
    for key in ("bank", "card"):
        account_id = accounts[key]
        for first in _month_starts(today):
            last = date(first.year, first.month,
                        monthrange(first.year, first.month)[1])
            # The current month's statement ends today, the way a real one
            # downloaded mid-month does - it must not claim to cover days
            # that have not happened.
            last = min(last, today)
            members = [r for r in rows if r.account_id == account_id
                       and first <= r.txn_date <= last]
            if not members:
                continue
            out.append((Statement(
                id=str(uuid.uuid4()),
                account_id=account_id,
                source_filename=f"{key}-statement-{first:%Y-%m}.pdf",
                period_start=first,
                period_end=last,
                transactions=members,
            ), key))
    return out


def workspace_for(db: Database, user_id: str) -> str | None:
    """The demo workspace owned by `user_id`, if it has one."""
    with db.identity_connection() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE demo_of = ? LIMIT 1", (user_id,)
        ).fetchone()
    return str(row["id"]) if row else None


def ensure_workspace(db: Database, user_id: str, name: str = "") -> str:
    """The demo workspace for this account, created and seeded if absent.

    Idempotent, and cheap on the second call: an existing workspace with rows
    in it is returned untouched, so turning Demo off and on again does not
    rebuild it - and any recategorising done during a demo is still there
    next time.
    """
    existing = workspace_for(db, user_id)
    if existing:
        with tenant_scope(existing):
            if repo.count_transactions(db) > 0:
                return existing
        seed(db, existing)
        return existing

    with db.identity_connection() as conn:
        row = conn.execute(
            """INSERT INTO users (google_sub, email, email_verified, name,
                                  picture, onboarding_step, onboarded_at,
                                  demo_of)
               VALUES (?, ?, TRUE, ?, '', 'done', fa_now(), ?)
               RETURNING id""",
            # A subject Google will never issue and an address on the reserved
            # .invalid domain: this row is a workspace, not a person, and
            # nothing must ever be able to sign in as it.
            (f"demo-workspace:{user_id}",
             f"demo-{str(user_id)[:8]}@demo.invalid",
             f"{name or 'Demo'} · demo data", str(user_id)),
        ).fetchone()
    workspace = str(row["id"])
    log.info("created demo workspace %s for %s", workspace, user_id)
    seed(db, workspace)
    return workspace


def seed(db: Database, workspace_id: str, today: date | None = None
         ) -> dict[str, Any]:
    """Fill a demo workspace with generated statements.

    Runs the same analytics the import pipeline runs - recurring detection and
    accounting-period assignment - so the demo is not a set of numbers that
    merely look right. It is the app's own output over generated input.
    """
    from .analytics.periods import assign_accounting_months
    from .analytics.recurring import detect_recurring

    today = today or date.today()
    with tenant_scope(workspace_id):
        specs = _accounts()
        ids = {key: repo.upsert_account(db, account)
               for key, account in specs.items()}

        rows, balances, pairs = build_rows(ids, today)

        # Statements first: a transaction's statement_id is a foreign key.
        statement_of: dict[str, str] = {}
        for statement, key in _statements(ids, rows, today):
            saved = repo.save_statement(db, statement, ids[key])
            for member in statement.transactions:
                statement_of[member.id] = saved
        for row in rows:
            row.statement_id = statement_of.get(row.id)

        series = detect_recurring(rows)
        # Which month each row is COUNTED in, by the same rule every imported
        # ledger goes through - the salary drift above is the reason this
        # matters, and hardcoding the answer would prove nothing.
        assign_accounting_months(
            rows, series,
            {sid: (s.period_start, s.period_end)
             for s, _ in _statements(ids, rows, today)
             for sid in [statement_of.get(s.transactions[0].id)] if sid},
        )

        repo.save_transactions(db, rows)
        if series:
            repo.save_recurring_series(db, series)
        if pairs:
            try:
                repo.save_transfer_pairs(db, [_Pair(**p) for p in pairs])
            except Exception:  # pragma: no cover - the pairing is not the ledger
                log.exception("could not record demo transfer pairs")

        for key, statement_id in (("bank", None), ("card", None)):
            repo.upsert_source_file(db, repo.SourceFileRecord(
                id=str(uuid.uuid4()),
                filename=f"{key}-statements-generated.pdf",
                file_hash=f"demo-{key}-{workspace_id}",
                source=SOURCE, parse_status="parsed",
                password_status="not_needed",
                institution_guess=specs[key].institution,
                account_type_guess=specs[key].account_type.value,
                account_id=ids[key],
                transaction_count=sum(1 for r in rows if r.account_id == ids[key]),
            ))

        log.info("seeded demo workspace %s with %d rows", workspace_id, len(rows))
        return {"transactions": len(rows), "accounts": len(ids),
                "months": MONTHS}


class _Pair:
    """The shape `save_transfer_pairs` expects, without importing its module."""

    def __init__(self, **fields: Any) -> None:
        for key, value in fields.items():
            setattr(self, key, value)


def reset(db: Database, workspace_id: str, today: date | None = None
          ) -> dict[str, Any]:
    """Throw the demo data away and generate it again.

    For when a demo has been walked all over - categories changed, rows
    excluded - and the next one should start clean.
    """
    with tenant_scope(workspace_id):
        # The widest scope there is: everything derived from documents, the
        # documents themselves, and the staging area. See CLEAR_SCOPES.
        db.clear("files")
    return seed(db, workspace_id, today)
