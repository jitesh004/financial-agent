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


class Category(str, Enum):
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

    category: Category = Category.UNCATEGORIZED
    category_source: ConfidenceSource = ConfidenceSource.DEFAULT
    category_confidence: float = 0.0

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
    def is_spend(self) -> bool:
        """True only for money that genuinely left the user's net worth."""
        return (
            self.direction == Direction.DEBIT
            and not self.is_internal_transfer
            and self.category not in NON_SPEND_CATEGORIES
        )


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
