"""What the word "EMI" in a narration actually means.

Card issuers print "EMI" against ordinary purchases as an *offer* marker. It
advertises that this particular charge is eligible to be converted into
instalments if the cardholder asks for it. Nothing has been borrowed, no
schedule exists, and the charge is the full price of whatever was bought:

    22:01 EMI INFINITIRETAILLIMITEDMumbai      34,990.00
    17:20 EMI TatadigitalGurgoan                5,349.00
    EMI CLOUDNINE PNEPPSPUNE                1,25,000.00
    EMI Riders Choice                           2,400.00

Reading that marker as a category was wrong twice over. It threw away the one
thing the row does say - the merchant - and replaced it with a payment method
that was never used; and it moved a hospital bill, a school fee, a fuel
fill-up and a bike-gear shop into the debt figures, which is where this app
reports what somebody actually owes. On one real ledger that was 41 rows worth
441,755 filed as borrowing that never happened.

So the rule here is that the token carries no weight on its own. A row is a
loan instalment only when it shows one of the shapes in LOAN_EVIDENCE, every
one of which is written by a LENDER collecting money rather than by an issuer
advertising a facility.

Three callers ask this question, for three different reasons, which is why the
vocabulary lives here rather than inside any one of them:

  categorize.rules            may an EMI rule claim this row?
  categorize.llm_categorizer  what merchant string does the model see, and may
                              it answer "emi" about that string?
  analytics.recurring         is this series a debt commitment or a shop the
                              user happens to visit monthly?
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# What a genuine loan instalment looks like
# ---------------------------------------------------------------------------

#: Lenders and lending arms whose name in the payee position is itself the
#: evidence. A mandate collected by one of these is servicing a loan; the same
#: mandate collected by a fund house is a SIP, which is why the payee and not
#: the mandate is what decides.
LENDER_TOKENS = (
    r"BAJAJ\s*FIN(?:ANCE|SERV)?|HDB\s*FINANCIAL|CHOLA(?:MANDALAM)?|SHRIRAM\s*FIN|"
    r"MAHINDRA\s*FIN|MUTHOOT|MANAPPURAM|TVS\s*CREDIT|CREDILA|AAVAS|HOME\s*FIRST|"
    r"HOMEFIRST|INDIABULLS\s*HOUSING|PNB\s*HOUSING|LIC\s*HOUSING|CAN\s*FIN|"
    r"FULLERTON|MONEYVIEW|KREDITBEE|NAVI\s*FIN|PAYSENSE|EARLYSALARY|"
    r"[A-Z]+\s*FINSERV|[A-Z]+\s*FINCORP|[A-Z]+\s*HFC"
)

#: Every shape that makes a row a loan instalment. Deliberately narrow: each
#: entry is something only a lender writes.
LOAN_EVIDENCE: tuple[str, ...] = (
    # A principal/interest split carrying its instalment number - "EMI PRIN
    # FOR TATA AIG GENERAL (020/036)". Only an amortization schedule is
    # written this way, and the counter is the part that cannot be faked by a
    # merchant whose name happens to start with "Principal".
    # The separator is written "/" on the statement and arrives as a space
    # once the description has been normalized, so both are accepted.
    r"\bEMI\s+(?:PRIN(?:CIPAL)?|INT(?:EREST)?)\b[^()]{0,60}"
    r"\(\s*\d+\s*[/\s]\s*\d+\s*\)",
    # The lender naming the product it is collecting against.
    r"\b(?:HOME|HOUSING|PERSONAL|AUTO|CAR|VEHICLE|TWO\s*WHEELER|GOLD|EDUCATION|"
    r"BUSINESS|CONSUMER|DURABLE|TOP\s*UP)\s+LOAN\b",
    r"\bMORTGAGE\b",
    r"\bLOAN\s+(?:REPAYMENT|INSTAL?MENT|EMI|ACCOUNT|A/?C|DISBURS\w*)\b",
    r"\bREPAYMENT\s+OF\s+LOAN\b|\bLOAN\s+CLOSURE\b|\bFORECLOSURE\b",
    # A loan account number. HL/PL/AL are how every Indian lender prefixes one.
    r"\b(?:HL|PL|AL|VL|BL|GL|LN|LAP|CDL)\d{4,}\b",
    # An ACH / NACH / ECS / standing-instruction mandate collected by a lender.
    rf"\b(?:ACH|NACH|ECS|SI)\b[^A-Za-z0-9]{{0,4}}(?:D[RB]?\b)?.{{0,40}}?"
    rf"\b(?:{LENDER_TOKENS})\b",
    # The lender's own wording for taking, missing or reversing an instalment.
    r"\bINSTAL?MENT\s+(?:DUE|RECOVERY|COLLECTION|BOUNCE|RETURN)\b",
    r"\bEMI\s+(?:DEBIT|RECOVERY|COLLECTION|BOUNCE|RETURN|PAYMENT\s+TO)\b",
    r"\bEMI\s+(?:FOR|OF)\s+LOAN\b|\bLOAN\s+EMI\b",
    # An instalment counter standing on its own - "INSTALMENT 24 OF 60".
    r"\bINSTAL?MENTS?\b[^A-Za-z]{0,6}\d{1,3}\s*(?:OF|/)\s*\d{1,3}\b",
)

LOAN_INSTALMENT = re.compile("|".join(LOAN_EVIDENCE), re.IGNORECASE)

#: A bare "instalment" with nothing else to say what is being paid off. On its
#: own that IS usually a loan - but the same word is how a recurring deposit,
#: a SIP and a card's own conversion FEE are all written, and each of those
#: has its own correct category. So the bare token counts as evidence only
#: when none of NOT_A_LOAN is in the same narration.
#:
#: This split is the fix for two rows that were being called debt: "RD
#: INSTALMENT 15,000" (a recurring deposit - saving, not borrowing) and
#: "INSTALLMENT PROCESSING FEE 199" (the fee for setting a conversion up,
#: which is a charge and not the instalment itself). Both matched the bare
#: \bINSTAL?MENT\b that the EMI rule used to carry, and both were claimed by
#: it before the investment and fee rules further down ever ran.
BARE_INSTALMENT = re.compile(r"\bINSTAL?MENTS?\b", re.IGNORECASE)

NOT_A_LOAN = re.compile(
    r"\bRD\b|\bRECURRING\s+DEPOSIT\b|\bFD\b|\bFIXED\s+DEPOSIT\b|\bSIP\b|"
    r"\bMUTUAL\s+FUND\b|\bELSS\b|\bNPS\b|\bPPF\b|\bSUKANYA\b|\bCHIT\b|"
    r"\bPREMIUM\b|\bPOLICY\b|\bINSURANCE\b|"
    r"\bPROCESSING\s+(?:FEE|CHARGE)|\bCONVERSION\s+(?:FEE|CHARGE)|"
    r"\bGST\b|\bCGST\b|\bSGST\b|\bIGST\b",
    re.IGNORECASE,
)


#: A named lender standing in the payee position, with no mandate marker in
#: front of it. Kept OUT of `LOAN_INSTALMENT` on purpose, because the two
#: questions this module answers are not the same question.
#:
#: "May the EMI rule claim this row?" has to stay narrow. That rule is
#: checked above Dining, Fuel, Shopping and Healthcare, so anything it takes,
#: those never see - and a gold-loan lender also sells gold, an NBFC also
#: sells insurance. A bare "MUTHOOT FINANCE" debit is not enough to overrule
#: them.
#:
#: "May the model's `emi` answer stand?" is looser, and can be: the model has
#: read the whole string and reached a conclusion, and all that is being
#: checked is that it has not called a plain SHOP a loan. A named NBFC is not
#: a shop, so its presence is enough to let the answer through.
LENDER_PAYEE = re.compile(rf"\b(?:{LENDER_TOKENS})\b", re.IGNORECASE)


def looks_like_loan_instalment(*texts: str | None) -> bool:
    """Is any of these narrations a lender collecting an instalment?

    Several strings are accepted because the evidence can survive in one and
    not another. Normalization strips rail prefixes and flattens separators,
    so "ACH-D- BAJAJ FINANCE LTD" reaches the analytics layer as "D BAJAJ
    FINANCE LTD" - with the mandate marker, which is half the evidence, gone.
    Passing both the raw and the normalized description is what covers that.
    """
    for text in texts:
        if not text:
            continue
        if LOAN_INSTALMENT.search(text):
            return True
        if BARE_INSTALMENT.search(text) and not NOT_A_LOAN.search(text):
            return True
    return False


def names_a_lender(*texts: str | None) -> bool:
    """Whether anything here is a lender rather than a merchant.

    The looser of the two tests - see LENDER_PAYEE for why there are two.
    """
    if looks_like_loan_instalment(*texts):
        return True
    return any(LENDER_PAYEE.search(t) for t in texts if t)


# ---------------------------------------------------------------------------
# Removing the marker
# ---------------------------------------------------------------------------

#: A standalone "EMI" token, wherever it sits. Bounded on both sides so
#: "EMIRATES", "SEMI" and "EMI-PRIN" (which the loan check has already
#: claimed) are left alone. The optional clock time is HDFC's - it prints the
#: transaction time in front of the marker, so a prefix-anchored pattern that
#: did not know about it missed every row on that issuer's statements.
_OFFER_MARKER = re.compile(
    r"(?:^|(?<=\s))(?:\d{1,2}[:.]\d{2}(?::\d{2})?\s+)?EMI(?:\s*[-:]\s*|\s+|$)",
    re.IGNORECASE,
)

_MULTISPACE = re.compile(r"\s+")


def strip_offer_marker(text: str | None) -> str:
    """The narration with the issuer's EMI offer marker taken out.

    Returns the text unchanged when it carries loan evidence: there the word
    is part of what the row means, and removing it would hide a real
    commitment. Returns it unchanged too when the marker is all there is -
    a row reading only "EMI" has no merchant to expose, and blanking it would
    turn a poor description into no description at all.
    """
    if not text:
        return ""
    if looks_like_loan_instalment(text):
        return text
    stripped = _MULTISPACE.sub(" ", _OFFER_MARKER.sub(" ", text)).strip()
    return stripped or text


def carries_offer_marker(text: str | None) -> bool:
    """Whether this narration has an EMI marker that means nothing.

    True for the issuer's advertisement, false both for a genuine instalment
    and for a narration with no EMI in it at all.
    """
    if not text or looks_like_loan_instalment(text):
        return False
    return bool(_OFFER_MARKER.search(text))
