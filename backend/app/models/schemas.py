"""Canonical domain model.

Everything that enters the system - PDF, XLSX, DOCX, CSV - is normalized into
these types before it touches storage or analytics. If a field isn't here, no
downstream code is allowed to depend on it.

Money rule: every amount is a Decimal. Never float. Rounding errors in a ledger
are indistinguishable from parsing bugs, and we need to be able to tell them apart.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourceFormat(str, Enum):
    PDF = "pdf"
    XLSX = "xlsx"
    XLS = "xls"
    CSV = "csv"
    DOCX = "docx"
    UNKNOWN = "unknown"


class AccountType(str, Enum):
    """What kind of instrument the statement describes.

    The distinction drives sign conventions: on a SAVINGS account a debit
    reduces net worth, on a CREDIT_CARD a debit *increases* a liability, and on
    a LOAN the balance is money owed rather than money held.
    """

    SAVINGS = "savings"
    CURRENT = "current"
    CREDIT_CARD = "credit_card"
    HOME_LOAN = "home_loan"
    PERSONAL_LOAN = "personal_loan"
    AUTO_LOAN = "auto_loan"
    INVESTMENT = "investment"
    WALLET = "wallet"
    UNKNOWN = "unknown"


LIABILITY_TYPES = {
    AccountType.CREDIT_CARD,
    AccountType.HOME_LOAN,
    AccountType.PERSONAL_LOAN,
    AccountType.AUTO_LOAN,
}

LOAN_TYPES = {
    AccountType.HOME_LOAN,
    AccountType.PERSONAL_LOAN,
    AccountType.AUTO_LOAN,
}


class Direction(str, Enum):
    CREDIT = "credit"  # money in
    DEBIT = "debit"    # money out


class Category:
    """Deliberately coarse. A person can reason about 25 buckets, not 200.

    TRANSFER and CC_PAYMENT are not spending. They exist so the analytics layer
    has something to exclude - see reconcile.transfers.
    """

    SALARY = "salary"
    OTHER_INCOME = "other_income"
    INTEREST_INCOME = "interest_income"
    REFUND = "refund"

    GROCERIES = "groceries"
    DINING = "dining"
    TRANSPORT = "transport"
    FUEL = "fuel"
    SHOPPING = "shopping"
    UTILITIES = "utilities"
    RENT = "rent"
    HEALTHCARE = "healthcare"
    INSURANCE = "insurance"
    EDUCATION = "education"
    ENTERTAINMENT = "entertainment"
    SUBSCRIPTIONS = "subscriptions"
    TRAVEL = "travel"
    PERSONAL_CARE = "personal_care"
    HOUSEHOLD = "household"
    GIFTS_DONATIONS = "gifts_donations"

    EMI = "emi"
    LOAN_INTEREST = "loan_interest"
    INVESTMENT = "investment"
    TAX = "tax"
    FEES_CHARGES = "fees_charges"
    CASH_WITHDRAWAL = "cash_withdrawal"

    #: Money sent to a named individual rather than a merchant - the bulk of
    #: UPI activity in India. This IS spending (it leaves your net worth),
    #: unlike TRANSFER, which moves money between your own accounts.
    P2P_TRANSFER = "p2p_transfer"

    TRANSFER = "transfer"          # between the user's own accounts
    CC_PAYMENT = "cc_payment"      # bank -> own credit card
    UNCATEGORIZED = "uncategorized"

    @classmethod
    def all_builtins(cls) -> list[str]:
        return [
            v for k, v in cls.__dict__.items()
            if not k.startswith("_") and isinstance(v, str)
        ]


INCOME_CATEGORIES = {
    Category.SALARY,
    Category.OTHER_INCOME,
    Category.INTEREST_INCOME,
    Category.REFUND,
}

#: Money that left an account but did not leave the user's net worth.
NON_SPEND_CATEGORIES = {
    Category.TRANSFER,
    Category.CC_PAYMENT,
    Category.INVESTMENT,
}

#: Categories that describe money going OUT. A credit carrying one of these
#: is a reversal of that spending - a returned purchase, a cancelled booking,
#: a refunded fee - and nets against it rather than counting as income.
#:
#: Deliberately excludes UNCATEGORIZED and P2P_TRANSFER: a credit from a named
#: individual, or one nothing could classify, is genuinely ambiguous. Those
#: stay income (the safe direction - never silently erase real money) and go
#: to the review queue instead.
SPENDING_CATEGORIES = {
    Category.GROCERIES, Category.DINING, Category.TRANSPORT, Category.FUEL,
    Category.SHOPPING, Category.UTILITIES, Category.RENT, Category.HEALTHCARE,
    Category.INSURANCE, Category.EDUCATION, Category.ENTERTAINMENT,
    Category.SUBSCRIPTIONS, Category.TRAVEL, Category.PERSONAL_CARE,
    Category.HOUSEHOLD, Category.GIFTS_DONATIONS, Category.EMI,
    Category.LOAN_INTEREST, Category.TAX, Category.FEES_CHARGES,
    Category.CASH_WITHDRAWAL,
}

class FlowRole(str, Enum):
    """Which side of the books a transaction lands on.

    Category says what money was spent ON; this says whether it was spent at
    all. The two are genuinely independent, and conflating them is what let a
    credit-card bill payment - money moving between two of the user's own
    accounts - be counted as income the moment the matching bank statement
    happened to be missing.

    Exactly one role per transaction, so every figure on the dashboard is a
    sum over a disjoint set rather than a sequence of overlapping filters.
    """

    #: Money that genuinely entered the user's net worth.
    INCOME = "income"
    #: Money that genuinely left it.
    EXPENSE = "expense"
    #: The funding leg of a move between the user's own accounts.
    TRANSFER_OUT = "transfer_out"
    #: The receiving account's record of that same money. Real, but already
    #: counted once on the way out.
    TRANSFER_IN = "transfer_in"
    #: A "payment received" on a card statement: settles a liability, and is
    #: never income no matter who funded it.
    CARD_SETTLEMENT = "card_settlement"
    #: Money coming back against an expense that was never the user's -
    #: someone repaying a purchase made on their card. A contra-expense, not
    #: income: the purchase it offsets is already counted as spending, so
    #: subtracting here nets the pair to zero.
    CLAIM_SETTLEMENT = "claim_settlement"
    #: Moved into an investment. Still the user's money, so not spending.
    INVESTMENT = "investment"
    #: A merchant giving money back. Also a contra-expense - counting it as
    #: income inflates both sides of the ledger for what was really a
    #: cancelled purchase.
    REFUND = "refund"
    #: The user explicitly took this row out of every total.
    EXCLUDED = "excluded"


#: Roles that reduce spending rather than adding to income. Both offset an
#: expense that is already in the totals, so they net against it.
CONTRA_EXPENSE_ROLES = {FlowRole.CLAIM_SETTLEMENT, FlowRole.REFUND}

#: Roles that are not a flow of the user's own money in either direction.
NEUTRAL_ROLES = {
    FlowRole.TRANSFER_OUT, FlowRole.TRANSFER_IN,
    FlowRole.CARD_SETTLEMENT, FlowRole.EXCLUDED,
}


#: Human-facing grouping used by the dashboard.
CATEGORY_GROUPS: dict[str, list[Category]] = {
    "Income": [Category.SALARY, Category.OTHER_INCOME, Category.INTEREST_INCOME, Category.REFUND],
    "Essentials": [
        Category.GROCERIES, Category.UTILITIES, Category.RENT, Category.TRANSPORT,
        Category.FUEL, Category.HEALTHCARE, Category.INSURANCE, Category.EDUCATION,
        Category.HOUSEHOLD,
    ],
    "Lifestyle": [
        Category.DINING, Category.SHOPPING, Category.ENTERTAINMENT, Category.SUBSCRIPTIONS,
        Category.TRAVEL, Category.PERSONAL_CARE, Category.GIFTS_DONATIONS,
    ],
    "Debt": [Category.EMI, Category.LOAN_INTEREST, Category.CC_PAYMENT],
    "Wealth": [Category.INVESTMENT],
    "Other": [
        Category.TAX, Category.FEES_CHARGES, Category.CASH_WITHDRAWAL,
        Category.P2P_TRANSFER, Category.TRANSFER, Category.UNCATEGORIZED,
    ],
}


class ConfidenceSource(str, Enum):
    """How a categorization decision was reached. Surfaced in the UI so a user
    can tell a hard rule from a model guess."""

    RULE = "rule"
    MERCHANT_CACHE = "merchant_cache"
    LLM = "llm"
    USER = "user"
    DEFAULT = "default"


class Account(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    id: str | None = None
    institution: str = "Unknown"
    account_type: AccountType = AccountType.UNKNOWN
    #: Last 4 digits only. Full numbers are redacted at ingestion, never stored.
    account_number_masked: str = ""
    #: The card's own product name ("Rewards", "Regalia"), when the statement
    #: prints one. Distinguishes several cards from the same bank in the UI,
    #: and doubles as a fallback identity key for an issuer that masks its
    #: card number so completely no digit survives extraction (see
    #: graph.nodes._account_identity).
    product_name: str | None = None
    holder_name: str | None = None
    currency: str = "INR"

    #: Latest known balance for an ASSET account (savings, current, wallet).
    #: Kept separate from principal_outstanding so net worth never has to guess
    #: which sign convention a number arrived in.
    current_balance: Decimal | None = None

    #: The statement period this balance was read from. Statements do not
    #: always arrive in chronological order - Gmail search, a batch upload, a
    #: single-file retry can all process an old month after a newer one - so
    #: merging two accounts has to compare dates rather than assume whichever
    #: file was seen first (or last) is the current figure. Without this,
    #: "first non-null wins" silently locked every account's balance to
    #: whichever statement happened to be parsed first, however old.
    balance_as_of: date | None = None

    #: Loan-specific, populated from loan statements when present.
    principal_outstanding: Decimal | None = None
    interest_rate: Decimal | None = None
    emi_amount: Decimal | None = None
    tenure_months_remaining: int | None = None

    #: Credit-card specific.
    credit_limit: Decimal | None = None

    @property
    def is_liability(self) -> bool:
        return self.account_type in LIABILITY_TYPES

    @property
    def balance(self) -> Decimal | None:
        """Signed balance: positive is owned, negative is owed."""
        if self.is_liability:
            return None if self.principal_outstanding is None else -self.principal_outstanding
        return self.current_balance

    def display_name(self) -> str:
        label = self.account_type.value.replace("_", " ").title()
        variant = f" {self.product_name}" if self.product_name else ""
        suffix = f" ({self.account_number_masked})" if self.account_number_masked else ""
        return f"{self.institution}{variant} {label}{suffix}"


class Transaction(BaseModel):
    """One row of one statement, normalized.

    `raw_description` is preserved verbatim forever. Categorization is a lossy
    interpretation and we must always be able to re-derive it from the source.
    """

    model_config = ConfigDict(use_enum_values=False)

    id: str | None = None
    account_id: str | None = None
    statement_id: str | None = None

    txn_date: date
    value_date: date | None = None
    raw_description: str
    normalized_description: str = ""
    merchant: str | None = None

    amount: Decimal  # always positive; `direction` carries the sign
    direction: Direction
    balance_after: Decimal | None = None
    currency: str = "INR"

    category: str = Category.UNCATEGORIZED
    category_source: ConfidenceSource = ConfidenceSource.DEFAULT
    category_confidence: float = 0.0
    #: Which rule decided it, when a rule did. Empty otherwise.
    #: `category_source` says a rule fired; this says which one.
    category_rule: str = ""
    #: Why this row is money in or money out - a code from rules.directions.
    #: Direction is the one field where a mistake lands on both sides of every
    #: total at once, so it carries its reasoning.
    direction_reason: str = ""

    #: Set by reconcile.transfers when this row is one leg of an internal move.
    is_internal_transfer: bool = False
    #: The *duplicate* leg of such a move - the destination account's record of
    #: money the source account already reported leaving. Both legs are real
    #: rows on real statements, but only one of them is a cash movement, so
    #: cashflow, recurring detection and forecasting must count exactly one.
    #: Spending analysis excludes both (see `is_spend`); cashflow excludes only
    #: the mirror. Conflating these two ideas is what makes a "committed
    #: outflow" figure come out at double the real EMI.
    is_mirror_leg: bool = False
    transfer_pair_id: str | None = None

    #: Set by analytics.recurring when this row belongs to a detected series.
    recurring_series_id: str | None = None

    reference: str | None = None
    source_row: int | None = None

    #: Content identity - account, date, amount, direction, description - as
    #: opposed to `id`, which is a fresh uuid on every parse. Everything the
    #: user authors (a corrected category, a note, an exclusion) hangs off
    #: this, so their decisions survive re-processing the same statement.
    #: See pipeline.fingerprint.
    fingerprint: str = ""

    #: The period this row is reported in (YYYY-MM). Usually the calendar
    #: month of `txn_date`, but not always: a salary paid on the last working
    #: day arrives on the 31st one month and the 1st two months later, which
    #: would double-count one month and empty another. Set by
    #: analytics.periods; empty until then.
    accounting_month: str = ""

    #: Automatic classification was not confident enough to decide alone. The
    #: safe default has still been applied - this never leaves a figure
    #: missing - but the row is surfaced for the user to confirm or flip.
    needs_review: bool = False
    review_reason: str = ""

    #: Which side of the books this row lands on. Populated in Workstream 2;
    #: empty means "derive it from category and direction", which is what the
    #: existing `is_spend` property already does.
    flow_role: str = ""

    #: True when the user explicitly took this row out of all totals. Distinct
    #: from a transfer: a transfer is excluded because it is not a real flow,
    #: this is excluded because the user said so.
    excluded: bool = False

    #: Free-text note the user attached to this row.
    note: str = ""

    #: Where this row came from. A statement row is reconciled against the
    #: balances its own document printed; an email alert is not, and no total
    #: that mixes the two can be trusted unless it can tell them apart.
    source: str = "statement"

    #: Set when the statement covering this alert arrived later and replaced
    #: it. Kept rather than deleted - the alert really did arrive, and being
    #: able to see that a checked row superseded it is worth a column.
    superseded: bool = False

    @property
    def is_reconcilable(self) -> bool:
        """Whether this row belongs in a balance check at all.

        An alert has no opening or closing balance to tie to, so including one
        in the reconciliation gate would report the statement it sits beside as
        failing forever.
        """
        return self.source == "statement"

    @field_validator("amount")
    @classmethod
    def _amount_non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("amount must be positive; use `direction` for sign")
        return v

    @property
    def signed_amount(self) -> Decimal:
        """Positive for money in, negative for money out."""
        return self.amount if self.direction == Direction.CREDIT else -self.amount

    @property
    def role(self) -> FlowRole:
        """The stored role, or one derived from what else is known.

        `flow_role` is only populated once the accounting pass has run (or the
        user has set it by hand). Deriving it on demand keeps every reader
        working against rows that predate that pass, including anything loaded
        from a database written by an older version.
        """
        if self.flow_role:
            try:
                return FlowRole(self.flow_role)
            except ValueError:
                pass  # an unknown stored value falls through to derivation
        return derive_flow_role(self)

    @property
    def is_spend(self) -> bool:
        """True only for money that genuinely left the user's net worth.

        Kept as the compatibility surface over `role` - a good deal of the
        analytics layer is written in terms of it - but the role is now the
        thing that decides.
        """
        return self.role == FlowRole.EXPENSE


def derive_flow_role(txn: "Transaction") -> FlowRole:
    """Work out which side of the books a transaction belongs on.

    Ordered most-authoritative first, and the order is the substance:

    1. An explicit human exclusion beats everything. It is the only signal
       here that someone actually looked at the row.
    2. Transfer pairing beats category text. Cross-account evidence that a
       debit funded a card payment is far stronger than any narration, and it
       is what stops a bill payment being read as spending on one side and
       income on the other.
    3. A "payment received" on a CARD is never income, however it was funded.
       This is the specific fix for someone else paying the user's card and
       the app booking it as their salary: it reaches this branch whether or
       not a matching bank debit was ever found.
    4. Only then does the category get a say.
    """
    if txn.excluded:
        return FlowRole.EXCLUDED

    if txn.is_internal_transfer:
        return FlowRole.TRANSFER_IN if txn.is_mirror_leg else FlowRole.TRANSFER_OUT

    if txn.direction == Direction.CREDIT:
        if txn.category == Category.CC_PAYMENT:
            # A credit categorised as a card payment is the card's own record
            # of its bill being settled. Unmatched only means the funding
            # statement is missing or somebody else paid - never that money
            # arrived from nowhere.
            return FlowRole.CARD_SETTLEMENT
        if txn.category == Category.REFUND:
            return FlowRole.REFUND
        if txn.category in INCOME_CATEGORIES:
            return FlowRole.INCOME
        if txn.category in {Category.TRANSFER, Category.INVESTMENT}:
            return FlowRole.TRANSFER_IN
        if txn.category in SPENDING_CATEGORIES:
            # Money coming back under a category that describes SPENDING - a
            # returned purchase, a cancelled booking, a reversed fee. It is
            # the same event as a refund, just labelled by merchant rather
            # than recognised as a reversal, so it nets against the spending
            # it undoes rather than counting as earnings.
            #
            # On a real ledger this was 22 rows worth 85,208: a 48,181
            # "education" credit is a fee reversal, not income. Booking those
            # as earnings inflated income AND left the original spending
            # standing, overstating net savings by the whole amount.
            return FlowRole.REFUND
        # A genuinely unexplained credit - uncategorized, or from a named
        # individual. Treated as income deliberately: the alternative
        # silently removes real money from the user's income, and a figure
        # that is too high is visible where one that is too low is not. The
        # review queue is what narrows these down.
        return FlowRole.INCOME

    if txn.category == Category.INVESTMENT:
        return FlowRole.INVESTMENT
    if txn.category == Category.TRANSFER:
        return FlowRole.TRANSFER_OUT
    if txn.category == Category.CC_PAYMENT:
        # If it reached here, it failed to match any card statement (or none was uploaded).
        # Without the itemized purchases, the bill payment itself is the only record of 
        # that spending. Dropping it would artificially deflate the user's spend by lakhs.
        return FlowRole.EXPENSE
    return FlowRole.EXPENSE


class ReconciliationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    #: Statement declared no opening/closing balance, so the check is unavailable.
    NOT_APPLICABLE = "not_applicable"


class ReconciliationResult(BaseModel):
    status: ReconciliationStatus
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    computed_closing: Decimal | None = None
    discrepancy: Decimal | None = None
    total_credits: Decimal = Decimal("0")
    total_debits: Decimal = Decimal("0")
    transaction_count: int = 0
    #: Rows where the running balance broke - the likeliest parse errors.
    suspect_rows: list[int] = Field(default_factory=list)
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status != ReconciliationStatus.FAILED


class Statement(BaseModel):
    """One uploaded file, after extraction and normalization."""

    model_config = ConfigDict(use_enum_values=False)

    id: str | None = None
    account_id: str | None = None

    source_filename: str
    source_format: SourceFormat = SourceFormat.UNKNOWN
    file_hash: str = ""

    period_start: date | None = None
    period_end: date | None = None
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None

    transactions: list[Transaction] = Field(default_factory=list)
    reconciliation: ReconciliationResult | None = None

    #: Which extraction strategy produced the rows. Kept for debugging bad parses.
    extractor_used: str = ""
    parse_warnings: list[str] = Field(default_factory=list)
    ingested_at: datetime = Field(default_factory=datetime.now)

    #: Anything the extractor found that the canonical model has no home for.
    extra: dict[str, Any] = Field(default_factory=dict)


class ExtractedTable(BaseModel):
    """Format-agnostic handoff between an extractor and the normalizer.

    Extractors understand file formats. The normalizer understands finance.
    This type is the only thing they share, which is what lets a new file format
    be added without touching normalization logic.
    """

    rows: list[list[str]] = Field(default_factory=list)
    header: list[str] | None = None
    source_page: int | None = None
    source_sheet: str | None = None
    confidence: float = 0.5
    #: Text found outside the table - statement headers, account numbers, loan
    #: summary blocks. Mined for account metadata.
    surrounding_text: str = ""


class ExtractionResult(BaseModel):
    """What every extractor returns, regardless of file format."""

    tables: list[ExtractedTable] = Field(default_factory=list)
    full_text: str = ""
    extractor_used: str = ""
    source_format: SourceFormat = SourceFormat.UNKNOWN
    warnings: list[str] = Field(default_factory=list)
    #: False when the file needs a password we weren't given.
    needs_password: bool = False
