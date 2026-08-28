"""Deterministic categorization rules.

Rules run before anything else and settle the large majority of transactions.
That matters for three reasons beyond cost: rules are instant, they are stable
(the same merchant lands in the same bucket every single run), and they are
auditable - a user can be shown exactly why something was classified.

Order is significant. The first matching rule wins, so specific patterns must
precede general ones: "HDFC HOME LOAN EMI" has to be seen as EMI before the
bare "HDFC" is seen as a bank transfer.

Patterns are matched against the NORMALIZED description (rails stripped,
uppercased) as well as the raw one, because rail prefixes hide merchant names.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..models.schemas import Category, ConfidenceSource, Direction, Transaction


@dataclass(frozen=True)
class Rule:
    pattern: re.Pattern[str]
    category: Category
    confidence: float = 0.95
    #: Restrict the rule to one direction. "INTEREST" means income on a savings
    #: statement and an expense on a loan statement - direction disambiguates.
    direction: Direction | None = None
    label: str = ""


def _r(pattern: str, category: Category, confidence: float = 0.95,
       direction: Direction | None = None, label: str = "") -> Rule:
    return Rule(re.compile(pattern, re.IGNORECASE), category, confidence,
                direction, label or pattern[:40])


#: Evaluated top to bottom. Most specific first.
RULES: list[Rule] = [
    # ---- Income --------------------------------------------------------
    # Indian payroll narrations run the tokens together, so \bSALARY\b never
    # matches "PRIVATELIMI-JITESHSALNOV25//CMS3". Anchoring SAL on a following
    # month abbreviation and year is specific enough not to catch "SALE".
    _r(r"\bSALARY\b|\bSAL\s+CREDIT\b|\bPAYROLL\b|\bWAGES\b|\bSALARY\s+CREDIT\b"
       r"|SAL(?=(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2})"
       r"|\bSAL[-/]|[-/]SAL\b|\bMONTHLY\s+SAL\b",
       Category.SALARY, 0.97, Direction.CREDIT),
    _r(r"\bBONUS\b|\bINCENTIVE\b|\bPERFORMANCE\s+PAY\b|\bCOMMISSION\b",
       Category.SALARY, 0.9, Direction.CREDIT),
    _r(r"\bINTEREST\s+(?:CAPITALISED|CREDIT|PAID)\b|\bCREDIT\s+INTEREST\b|"
       r"\bSAVINGS\s+INTEREST\b|\bFD\s+INTEREST\b|\bINT\.?\s*CR\b",
       Category.INTEREST_INCOME, 0.95, Direction.CREDIT),
    _r(r"\bDIVIDEND\b", Category.OTHER_INCOME, 0.95, Direction.CREDIT),
    _r(r"\bREFUND\b|\bREVERSAL\b|\bCASHBACK\b|\bCHARGEBACK\b|\bRETURN\b",
       Category.REFUND, 0.9, Direction.CREDIT),
    _r(r"\bFREELANCE\b|\bCONSULTING\b|\bINVOICE\b|\bPROFESSIONAL\s+FEES\b",
       Category.OTHER_INCOME, 0.85, Direction.CREDIT),

    # ---- Debt ----------------------------------------------------------
    # Must precede investment and generic bank rules.
    _r(r"\bHOME\s+LOAN\b|\bHOUSING\s+LOAN\b|\bMORTGAGE\b|\bHL\d{4,}\b",
       Category.EMI, 0.96),
    _r(r"\bPERSONAL\s+LOAN\b|\bPL\d{4,}\b|\bCONSUMER\s+LOAN\b", Category.EMI, 0.96),
    _r(r"\bAUTO\s+LOAN\b|\bCAR\s+LOAN\b|\bVEHICLE\s+LOAN\b|\bTWO\s*WHEELER\s+LOAN\b",
       Category.EMI, 0.96),
    # Bare "EMI" is deliberately NOT matched here. HDFC prints the literal
    # word "EMI" as a prefix on any ONE-TIME purchase the cardholder (or a
    # merchant offer) converted to the card's own installment plan - a
    # hospital bill, a fuel fill-up, a dinner, a wine shop run all carried it:
    # "EMI CLOUDNINE PNEPPSPUNE 1,25,000.00" sits in the statement right next
    # to "ZEPTO MARKETPLACE... 164.54" with no other marker distinguishing
    # them. That is a PAYMENT METHOD, not what the money was for, and matching
    # it here - checked before Dining/Fuel/Shopping/Healthcare/Education -
    # pre-empted every one of those more specific, more correct rules. A real
    # loan's principal/interest breakdown is the opposite: narrow and
    # unmistakable ("EMI PRIN FOR TATA AIG GENERAL", "EMI INT-..."), so only
    # that shape is matched as EMI; a bare "EMI <merchant>" now falls through
    # to whatever rule actually describes the purchase.
    # "(020/036)" is installment 20 of 36 - the one unambiguous marker of a
    # genuine amortization schedule, as opposed to a merchant name that
    # happens to start with "Principal" or "Interest".
    _r(r"\bEMI\s+(?:PRIN(?:CIPAL)?|INT(?:EREST)?)\b.*\(\s*\d+\s*/\s*\d+\s*\)"
       r"|\bINSTAL?MENT\b|\bLOAN\s+REPAYMENT\b|\bACH[-\s]?D\b.*\bLOAN\b",
       Category.EMI, 0.9),
    _r(r"\bINTEREST\s+CHARGED\b|\bFINANCE\s+CHARGE\b|\bINTEREST\s+DEBIT\b",
       Category.LOAN_INTEREST, 0.95, Direction.DEBIT),
    # CRED is a credit-card bill payment app, and BPPY is how HDFC labels a
    # BillPay settlement on the card itself. Both settle spending that the card
    # statement has already counted, so treating them as fresh spending
    # double-counts every bill: CRED alone accounted for 4.08 lakh.
    _r(r"\bCREDIT\s+CARD\s+PAYMENT\b|\bCARD\s+PAYMENT\b|\bPAYMENT\s+RECEIVED\b|"
       r"\bAUTOPAY\b.*\bCARD\b|\bCC\s+PAYMENT\b|\bBPPY\b|\bCRED\b|\bCRED\.CLUB\b|"
       r"\bBILLPAY\b|\bBBPS[\s\-]*PAYMENT\b|\bAMEX\b|\bAMERICAN\s+EXPRESS\b",
       Category.CC_PAYMENT, 0.93),
    # HDFC prints reward points inside the description, and a bill payment earns
    # none - so its row can reduce to nothing but a timestamp and a bare "+ C".
    # Every merchant row keeps either a name or a points count, which makes a
    # description of ONLY those two things a settled bill: 2.53 lakh of them
    # were being counted as card spending.
    _r(r"^\s*\d{1,2}:\d{2}\s*\+\s*C\s*$", Category.CC_PAYMENT, 0.85),

    # ---- Investments ---------------------------------------------------
    _r(r"\bSIP\b|\bMUTUAL\s+FUND\b|\bMF\s+PURCHASE\b|\bFOLIO\b|\bNAV\b|"
       r"\bPARAG\s+PARIKH\b|\bNIFTY\b|\bINDEX\s+FUND\b|\bFLEXI\s*CAP\b|"
       r"\bELSS\b|\bBSE\s+LTD\b|\bNSE\s+CLEARING\b",
       Category.INVESTMENT, 0.93),
    _r(r"\bZERODHA\b|\bGROWW\b|\bUPSTOX\b|\bICICI\s*DIRECT\b|\bANGEL\s*ONE\b|"
       r"\bKUVERA\b|\bCOIN\b|\bDEMAT\b|\bNPS\b|\bPPF\b|\bEPF\b|\bSUKANYA\b|"
       r"\bRECURRING\s+DEPOSIT\b|\bFIXED\s+DEPOSIT\b|\bRD\s+INSTAL?MENT\b",
       Category.INVESTMENT, 0.9),

    # ---- Housing & utilities -------------------------------------------
    _r(r"\bHOUSE\s+RENT\b|\bRENT\s+PAYMENT\b|\bMONTHLY\s+RENT\b|\bLANDLORD\b|"
       r"\bNOBROKER\b.*\bRENT\b|\bRENT\b",
       Category.RENT, 0.88),
    _r(r"\bBESCOM\b|\bMSEB\b|\bTATA\s+POWER\b|\bADANI\s+ELECTRICITY\b|"
       r"\bELECTRICITY\b|\bPOWER\s+BILL\b|\bBSES\b|\bTORRENT\s+POWER\b",
       Category.UTILITIES, 0.95),
    _r(r"\bWATER\s+(?:BILL|CHARGES)\b|\bBWSSB\b|\bMUNICIPAL\b|\bGAS\s+BILL\b|"
       r"\bINDANE\b|\bHP\s+GAS\b|\bMAHANAGAR\s+GAS\b|\bPIPED\s+GAS\b",
       Category.UTILITIES, 0.93),
    _r(r"\bAIRTEL\b|\bJIO\b|\bVODAFONE\b|\bVI\s+POSTPAID\b|\bBSNL\b|\bACT\s+FIBERNET\b|"
       r"\bBROADBAND\b|\bPOSTPAID\b|\bPREPAID\s+RECHARGE\b|\bMOBILE\s+RECHARGE\b|"
       r"\bHATHWAY\b|\bTIKONA\b",
       Category.UTILITIES, 0.92),
    _r(r"\bMAINTENANCE\s+CHARGES\b|\bSOCIETY\s+MAINTENANCE\b|\bAPARTMENT\s+DUES\b",
       Category.HOUSEHOLD, 0.9),

    # ---- Insurance & tax -----------------------------------------------
    _r(r"\bINSURANCE\b|\bPOLICY\s+PREMIUM\b|\bLIC\b|\bHDFC\s+LIFE\b|\bICICI\s+PRU\b|"
       r"\bMAX\s+LIFE\b|\bSTAR\s+HEALTH\b|\bBAJAJ\s+ALLIANZ\b|\bTERM\s+PLAN\b|"
       r"\bMEDICLAIM\b|\bPREMIUM\s+PAYMENT\b",
       Category.INSURANCE, 0.93),
    # "GST" alone is too broad - it appears inside "ANNUAL FEE - GST INCLUSIVE",
    # which is a bank charge, not a tax payment. Require tax-payment context.
    _r(r"\bINCOME\s+TAX\b|\bTDS\b|\bADVANCE\s+TAX\b|\bSELF\s+ASSESSMENT\b|"
       r"\bPROPERTY\s+TAX\b|\bCHALLAN\b|\bGST\s+PAYMENT\b|\bTAX\s+PAYMENT\b|"
       r"\bITNS\b|\bDIRECT\s+TAX\b",
       Category.TAX, 0.93),

    # ---- Bank fees ------------------------------------------------------
    _r(r"\bANNUAL\s+FEE\b|\bSERVICE\s+CHARGE\b|\bPROCESSING\s+FEE\b|\bLATE\s+FEE\b|"
       r"\bPENALTY\b|\bAMC\b|\bATM\s+CHARGE\b|\bSMS\s+CHARGE\b|\bCONVENIENCE\s+FEE\b|"
       r"\bMIN\s+BAL\b|\bNON\s+MAINTENANCE\b|\bOVERLIMIT\b|\bGST\s+INCLUSIVE\b",
       Category.FEES_CHARGES, 0.92),
    _r(r"\bATW\b|\bATM\s+(?:WDL|WITHDRAWAL|CASH)\b|\bCASH\s+WITHDRAWAL\b|\bNWD\b|"
       r"\bCASH\s+WDL\b",
       Category.CASH_WITHDRAWAL, 0.95),

    # ---- Food ------------------------------------------------------------
    _r(r"\bSWIGGY\b|\bZOMATO\b|\bEATSURE\b|\bDOMINOS\b|\bPIZZA\s+HUT\b|\bMCDONALD\b|"
       r"\bKFC\b|\bBURGER\s+KING\b|\bSUBWAY\b|\bSTARBUCKS\b|\bCAFE\b|\bCOFFEE\b|"
       r"\bRESTAURANT\b|\bBIRYANI\b|\bBARBEQUE\b|\bTRUFFLES\b|\bCHAAYOS\b|\bBARISTA\b|"
       r"\bDINER\b|\bBAKERY\b|\bDHABA\b|\bFOOD\s*COURT\b|\bTHIRD\s+WAVE\b",
       Category.DINING, 0.94),
    _r(r"\bBIGBASKET\b|\bBLINKIT\b|\bZEPTO\b|\bGROFERS\b|\bDMART\b|\bD\s*MART\b|"
       r"\bRELIANCE\s+FRESH\b|\bMORE\s+SUPERMARKET\b|\bSPENCER\b|\bNATURE\s*S\s+BASKET\b|"
       r"\bSUPERMARKET\b|\bKIRANA\b|\bGROCER\b|\bLICIOUS\b|\bFRESH\s*TO\s*HOME\b|"
       r"\bMILK\b|\bDAIRY\b|\bCOUNTRY\s+DELIGHT\b",
       Category.GROCERIES, 0.94),

    # ---- Transport --------------------------------------------------------
    _r(r"\bUBER\b|\bOLA\b|\bRAPIDO\b|\bMERU\b|\bBLUSMART\b|\bNAMMA\s+METRO\b|"
       r"\bMETRO\b|\bBMTC\b|\bBEST\b|\bDMRC\b|\bIRCTC\b|\bREDBUS\b|\bFASTAG\b|"
       r"\bTOLL\b|\bPARKING\b",
       Category.TRANSPORT, 0.93),
    _r(r"\bINDIAN\s*OIL\b|\bIOCL\b|\bBHARAT\s+PETROLEUM\b|\bBPCL\b|\bHPCL\b|"
       r"\bHP\s+PETROL\b|\bSHELL\b|\bRELIANCE\s+PETRO\b|\bPETROL\b|\bDIESEL\b|"
       r"\bFUEL\b|\bNAYARA\b",
       Category.FUEL, 0.94),

    # ---- Travel -----------------------------------------------------------
    _r(r"\bMAKEMYTRIP\b|\bGOIBIBO\b|\bYATRA\b|\bCLEARTRIP\b|\bIXIGO\b|\bEASEMYTRIP\b|"
       r"\bINDIGO\b|\bAIR\s*INDIA\b|\bVISTARA\b|\bSPICEJET\b|\bAKASA\b|\bEMIRATES\b|"
       r"\bAIRLINES\b|\bAIRWAYS\b|\bOYO\b|\bAIRBNB\b|\bBOOKING\s*\.?\s*COM\b|"
       r"\bMARRIOTT\b|\bTAJ\s+HOTEL\b|\bHOTEL\b|\bRESORT\b|\bTRAVEL\b",
       Category.TRAVEL, 0.9),

    # ---- Shopping ---------------------------------------------------------
    _r(r"\bAMAZON\b|\bFLIPKART\b|\bMYNTRA\b|\bAJIO\b|\bNYKAA\b|\bMEESHO\b|\bTATA\s+CLIQ\b|"
       r"\bSNAPDEAL\b|\bDECATHLON\b|\bIKEA\b|\bPEPPERFRY\b|\bURBAN\s+LADDER\b|"
       r"\bLIFESTYLE\b|\bSHOPPERS\s+STOP\b|\bPANTALOONS\b|\bZARA\b|\bH\s*&\s*M\b|"
       r"\bUNIQLO\b|\bCROMA\b|\bRELIANCE\s+DIGITAL\b|\bVIJAY\s+SALES\b|\bAPPLE\s+STORE\b",
       Category.SHOPPING, 0.92),

    # ---- Subscriptions ----------------------------------------------------
    _r(r"\bNETFLIX\b|\bSPOTIFY\b|\bAMAZON\s+PRIME\b|\bPRIME\s+VIDEO\b|\bHOTSTAR\b|"
       r"\bDISNEY\b|\bSONYLIV\b|\bZEE5\b|\bYOUTUBE\s+PREMIUM\b|\bAPPLE\s+MUSIC\b|"
       r"\bICLOUD\b|\bGOOGLE\s+ONE\b|\bDROPBOX\b|\bADOBE\b|\bMICROSOFT\s+365\b|"
       r"\bOFFICE\s*365\b|\bCHATGPT\b|\bOPENAI\b|\bANTHROPIC\b|\bCLAUDE\b|\bGITHUB\b|"
       r"\bNOTION\b|\bFIGMA\b|\bCANVA\b|\bSUBSCRIPTION\b|\bCULT\s*FIT\b|\bCULTFIT\b|"
       r"\bMEMBERSHIP\b",
       Category.SUBSCRIPTIONS, 0.93),

    # ---- Entertainment ----------------------------------------------------
    _r(r"\bBOOKMYSHOW\b|\bPVR\b|\bINOX\b|\bCINEPOLIS\b|\bCINEMA\b|\bMULTIPLEX\b|"
       r"\bDREAM11\b|\bGAMING\b|\bSTEAM\b|\bPLAYSTATION\b|\bXBOX\b|\bNINTENDO\b",
       Category.ENTERTAINMENT, 0.92),

    # ---- Health & care ----------------------------------------------------
    _r(r"\bAPOLLO\b|\bPRACTO\b|\b1MG\b|\bPHARMEASY\b|\bNETMEDS\b|\bMEDPLUS\b|"
       r"\bPHARMACY\b|\bCHEMIST\b|\bHOSPITAL\b|\bCLINIC\b|\bDIAGNOSTIC\b|\bLAB\b|"
       r"\bDOCTOR\b|\bDENTAL\b|\bMEDICAL\b|\bTHYROCARE\b|\bDR\s+LAL\b",
       Category.HEALTHCARE, 0.93),
    _r(r"\bSALON\b|\bSPA\b|\bBARBER\b|\bURBAN\s*COMPANY\b|\bURBANCLAP\b|\bLAKME\b|"
       r"\bGROOMING\b|\bGYM\b|\bFITNESS\b",
       Category.PERSONAL_CARE, 0.9),

    # ---- Education --------------------------------------------------------
    _r(r"\bSCHOOL\s+FEE\b|\bCOLLEGE\s+FEE\b|\bTUITION\b|\bUDEMY\b|\bCOURSERA\b|"
       r"\bBYJU\b|\bUNACADEMY\b|\bVEDANTU\b|\bUPGRAD\b|\bSCALER\b|\bEDUCATION\b|"
       r"\bACADEMY\b|\bINSTITUTE\b",
       Category.EDUCATION, 0.9),

    # ---- Giving -----------------------------------------------------------
    _r(r"\bDONATION\b|\bCHARITY\b|\bTEMPLE\b|\bTRUST\b|\bNGO\b|\bGOONJ\b|\bCRY\b|"
       r"\bMILAAP\b|\bKETTO\b",
       Category.GIFTS_DONATIONS, 0.9),

    # ---- Merchants found in this ledger -----------------------------------
    # Everything below was an uncategorised row on a real statement. Grouped by
    # what the money was for rather than by rail.
    _r(r"\bBSE\s*STAR\s*MF\b|\bBSESTARMF\b|\bINDIANCLEA\b|\bSMALLCASE\b|"
       r"\bMF\s*CENTRAL\b|\bKFINTECH\b|\bCAMSONLINE\b",
       Category.INVESTMENT, 0.93),
    # An ACH mandate paying a BANK is a loan instalment: a mandate to anyone
    # else (an insurer, a fund house) is caught by the rules above this one.
    _r(r"\bACH[/\-\s].*\bBANK\b|\bACH[/\-\s].*\bFINANCE\b|\bNACH[/\-\s].*\bBANK\b",
       Category.EMI, 0.82),
    _r(r"\bMSEDC\b|\bMAHADISCOM\b|\bMAHAVITARAN\b", Category.UTILITIES, 0.94),
    # ADYPU runs together with EDU on the statement, so a trailing \b never
    # matches - the same trap that hid ZOMATOnewdelhi from the dining rule.
    _r(r"\bADYPU\w*|\bUNIVERSITY\b|\bVIDYALAYA\b|\bJUNIOR\s*COLLEGE\b|\bEDU\b",
       Category.EDUCATION, 0.9),
    _r(r"\bCLOUDNINE\b|\bMEDICO\b|\bPATHOLOG\w*\b|\bORTHO\w*\b|\bSPECIALITY\s*CLI\w*\b|"
       r"\bWELLNESS\b|\bHEALTHCARE\b|\bNURSING\s*HOME\b",
       Category.HEALTHCARE, 0.9),
    _r(r"\bAGODA\b|\bTRIVAGO\b|\bTRAVELOGY\b|\bSUITES\b|\bLODGE\b|\bHOMESTAY\b",
       Category.TRAVEL, 0.9),
    # Eternal Ltd is Zomato's parent company and appears under that name on
    # card statements. "DISTRICT DINING" is its going-out arm.
    _r(r"\bETERNAL\s+LIMITED\b|\bDISTRICTDINING\b|\bDISTRICT\s+DINING\b|"
       r"\bWINES\b|\bSWEETS\b|\bFOODS\s+AND\b|\bTEA\s*ST\w*\b|\bJUICE\b|"
       r"\bMILKBASKET\b|\bCATERER\w*\b",
       Category.DINING, 0.88),
    _r(r"\bRELIANCE\s+RETAIL\b|\bRELIANCE\s+SMART\b", Category.GROCERIES, 0.92),
    _r(r"\bDISTRICT\s+MOVIE\b|\bMOVIE\s+TICKE\w*\b", Category.ENTERTAINMENT, 0.92),
    _r(r"\bTRAYA\b|\bMAMAEARTH\b|\bBOMBAY\s+SHAVING\b", Category.PERSONAL_CARE, 0.9),
    _r(r"\bEUREKA\s*FORBES\b|\bHOME\s*CENTRE\b|\bFURNITURE\b", Category.HOUSEHOLD, 0.88),
    _r(r"\bAUTO\s+INDUSTRIES\b|\bTYRES?\b|\bMOTORS?\b|\bSERVICE\s+CENTRE\b",
       Category.TRANSPORT, 0.85),
    # GST charged on a card fee is part of that fee, not a tax payment - the
    # TAX rules above already claimed anything that is genuinely a tax remittance.
    _r(r"\bCGST\b|\bSGST\b|\bIGST\b|\bPROCESSING\s+FEE|\bINSTALLMENT\s+PROCESSING\b",
       Category.FEES_CHARGES, 0.9),

    # ---- Generic transfer rails (lowest priority) -------------------------
    _r(r"\bSELF\b|\bOWN\s+ACCOUNT\b|\bTRANSFER\s+TO\s+SELF\b", Category.TRANSFER, 0.85),
]


#: A leading timestamp and/or "EMI" marker, ahead of the actual merchant.
#: Matches "22:01 EMI INFINITIRETAILLIMITEDMumbai" and "EMI Riders Choice".
#: Anchored, so an "EMI" appearing mid-description is left alone - only a
#: prefix is a conversion marker rather than part of the payee's name.
_EMI_PREFIX = re.compile(r"^\s*(?:\d{1,2}:\d{2}\s*)?EMI[\s\-]+", re.IGNORECASE)


def apply_rules(txn: Transaction) -> tuple[Category, float, str] | None:
    """Return (category, confidence, rule_label) for the first matching rule.

    Both the raw and normalized descriptions are searched, because normalization
    strips rail prefixes that sometimes carry the only useful signal (an "ATW"
    prefix is the only marker that a row is an ATM withdrawal).
    """
    haystacks = [txn.raw_description or "", txn.normalized_description or ""]

    # A purchase converted to instalments still names its merchant, and that
    # is what decides the category. HDFC prefixes any converted purchase with
    # "EMI" (often behind a timestamp), and the EMI rule was deliberately
    # narrowed so it would stop hijacking those rows from the merchant rules -
    # but nothing then put the merchant back within reach, so the whole family
    # fell through to uncategorized instead. On this ledger that was 41 rows
    # worth 441,755: Eduspark, Empire Foundation, Infiniti Retail, Panchjanya
    # Automobile - all obviously classifiable by name.
    for stripped in (_EMI_PREFIX.sub("", h) for h in list(haystacks)):
        if stripped and stripped not in haystacks:
            haystacks.append(stripped)

    for rule in RULES:
        if rule.direction is not None and rule.direction != txn.direction:
            continue
        if any(rule.pattern.search(h) for h in haystacks):
            return rule.category, rule.confidence, rule.label
    return None


def categorize_by_rules(transactions: list[Transaction]) -> int:
    """Apply rules in place. Returns how many transactions were settled.

    Transactions already categorized by transfer detection are left alone -
    cross-account evidence beats a text pattern every time.
    """
    settled = 0
    for txn in transactions:
        if txn.category != Category.UNCATEGORIZED:
            continue  # already decided by a stronger signal
        match = apply_rules(txn)
        if match is None:
            continue
        category, confidence, _label = match
        txn.category = category
        txn.category_source = ConfidenceSource.RULE
        txn.category_confidence = confidence
        settled += 1
    return settled


#: Tokens that mean the counterparty is a business, not a person. Checked
#: before the person-name shape below, because "LAXMI SUGANDHI WOR S" and
#: "GUPTA COLLECTION" both read as three capitalised words.
_ORGANISATION_TOKEN = re.compile(
    r"\b(?:PVT|PRIVATE|LTD|LIMITED|LLP|INC|CORP|CO|COMPANY|ENTERPRISE\w*|"
    r"TRADERS?|STORES?|SHOP|MART|AGENCY|AGENCIES|ASSOCIATES?|SERVICES?|"
    r"INDUSTRIES|COLLECTION|SOLUTIONS?|TECHNOLOG\w*|FOODS?|WINES|SWEETS|"
    r"HOTEL|RESTAURANT|MEDICAL|CLINIC|HOSPITAL|BANK|FINANCE|CAPITAL|"
    r"FOUNDATION|TRUST|SOCIETY|INFOTECH|DIGITAL|RETAIL|COMMUNICATIONS)\b",
    re.IGNORECASE,
)
#: UPI notes the payer types themselves. "Send Money" is the default label the
#: apps put on a person-to-person transfer.
_P2P_NOTE = re.compile(r"\bSEND\s*MONEY\b|\bPAY\s*TO\s*CONTACT\b", re.IGNORECASE)
#: An honorific is conclusive: only people get one.
_HONORIFIC = re.compile(r"\b(?:MR|MRS|MS|SHRI|SMT|DR)\b\.?\s+[A-Z]", re.IGNORECASE)
#: Two or three capitalised words and nothing else - "VIJAY BHOJA SHETTY".
_PERSON_NAME = re.compile(r"^[A-Z][A-Za-z]{1,14}(?:\s+[A-Z][A-Za-z]{1,14}){1,3}$")


#: Honorifics and titles, dropped before comparing names so "MR JITESH ..."
#: still lines up with "Jitesh ...".
_NAME_NOISE = frozenset({"MR", "MRS", "MS", "SHRI", "SMT", "SRI", "DR", "KUMARI"})


def _name_tokens(text: str) -> list[str]:
    """Name parts long enough to be worth comparing, honorifics removed."""
    return [t for t in re.split(r"[^A-Za-z]+", (text or "").upper())
            if len(t) >= 3 and t not in _NAME_NOISE]


def _payee_field(txn: Transaction) -> str:
    """The counterparty as the statement wrote it, before normalization.

    ICICI writes UPI/<payee>/<vpa>/<note>/<bank>/..., Yes Bank writes
    UPI_<PAYEE> IND - Ref No: <ref>. Both put the payee first.
    """
    raw = (txn.raw_description or "").strip()
    m = re.match(r"^UPI[/_]([^/]{2,40}?)(?:\s+IND\b|/|$)", raw, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def looks_like_person_payment(txn: Transaction) -> bool:
    """Whether this row is money handed to an individual rather than a shop.

    UPI made person-to-person payments the bulk of an Indian statement, and no
    merchant rule will ever match a name. Calling those "uncategorized" hides
    real spending behind a label that suggests a bug; calling them P2P says
    what actually happened and leaves the total honest.
    """
    raw = txn.raw_description or ""
    payee = _payee_field(txn)
    # The organisation check must look at the PAYEE alone. Run against the whole
    # row it matches the destination bank in "UPI/AJINKYA MA/.../HDFC BANK/..."
    # and rejects every ICICI person-to-person payment there is.
    subject = payee or raw
    if _ORGANISATION_TOKEN.search(subject):
        return False
    if _HONORIFIC.search(subject):
        return True
    if payee and _PERSON_NAME.match(payee):
        return True
    return bool(payee) and bool(_P2P_NOTE.search(raw))


def is_self_payment(txn: Transaction, holder_names: Sequence[str] | None) -> bool:
    """Whether the counterparty is the account holder themselves.

    Paying your own name is a transfer between your own accounts, not
    spending. Matched on surname-plus-given-name tokens rather than the whole
    string, because statements truncate ("UPI/Jitesh Muk/...") and reorder.
    """
    if not holder_names:
        return False
    payee = _payee_field(txn) or (txn.raw_description or "")[:48]
    payee_tokens = _name_tokens(payee)
    if not payee_tokens:
        return False
    collapsed = "".join(payee_tokens)

    for name in holder_names:
        held = _name_tokens(name)
        if len(held) < 2:
            # A single given name is far too weak to claim a payment is your
            # own - plenty of other people share it.
            continue
        # Every part of the holder's name appears in the payee. This is what
        # bridges the profile's "Jitesh Agarwal" to the "MR JITESH MUKESH
        # AGARWAL" the bank prints: the middle name is extra, not a mismatch.
        if all(any(p.startswith(h) or h.startswith(p) for p in payee_tokens)
               for h in held):
            return True
        # ICICI truncates the payee to ten characters, so the name arrives as a
        # prefix of itself with the spaces already gone.
        target = "".join(held)
        if len(collapsed) >= 8 and (target.startswith(collapsed)
                                    or collapsed.startswith(target)):
            return True
    return False


def fallback_category(
    txn: Transaction, holder_names: Sequence[str] | None = None
) -> Category:
    """Last-resort bucket when no rule and no model has an opinion.

    Direction alone is weak but honest: unexplained money in is income,
    unexplained money out stays uncategorized so it shows up in the UI as
    something the user should label rather than being hidden in a bucket.

    Two structural checks run first, because both are about WHO was paid rather
    than what for, which no merchant pattern can see.
    """
    if is_self_payment(txn, holder_names):
        return Category.TRANSFER
    if txn.direction == Direction.CREDIT:
        return Category.OTHER_INCOME
    if looks_like_person_payment(txn):
        return Category.P2P_TRANSFER
    return Category.UNCATEGORIZED
