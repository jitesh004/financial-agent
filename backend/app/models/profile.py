"""The user's own identity details.

Collected for one purpose only: deriving candidate passwords for the user's own
password-protected statements, and (optionally) confirming a statement belongs
to them rather than being uploaded by mistake.

This is genuinely sensitive data - PAN, date of birth - so three rules apply:

  1. It never leaves the machine. It is stored in the local SQLite file and is
     never included in anything sent to a language model or any network call.
  2. It is only ever used against files the USER uploaded. Nothing here helps
     open anyone else's documents.
  3. Every value is optional. The app works without a profile; a profile only
     unlocks protected PDFs and improves account matching.
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, ConfigDict, field_validator


class UserProfile(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    full_name: str = ""
    date_of_birth: date | None = None
    #: Uppercased and validated to the standard AAAAA9999A shape.
    pan: str = ""
    #: Digits only; the last 10 are kept for password templates.
    mobile: str = ""
    #: Free-text extra passwords the user knows work for their own files. Tried
    #: before the derived candidates, so an odd bank format is never a blocker.
    custom_passwords: list[str] = []

    #: Sender fragments to ignore permanently - a family member's account, or a
    #: business account that doesn't belong in a personal dashboard. Matched as
    #: substrings against the sender address, so "rbl.bank" covers every RBL
    #: mailer. Excluded statements are never downloaded or analysed, and are
    #: reported as deliberately ignored rather than as failures.
    excluded_senders: list[str] = []

    def is_excluded(self, sender: str) -> bool:
        """Whether a sender matches one of the user's permanent ignore rules.

        A bare keyword ("rbl.bank") is matched as a substring anywhere, which
        is fine - it only ever appears in one bank's mail. A full mailbox
        address is matched as a PREFIX of the sender's address instead:
        IndusInd sends a firm's current-account statements from
        "estatements@indusind.com" and the user's own credit card from
        "creditcard.estatements@indusind.com" - the first string is a plain
        substring of the second, so blocking "indusind" (or even the exact
        current-account address, as a substring) silently hid the user's own
        card along with the firm's account. Prefix matching still excludes the
        firm's exact address while leaving the card's different mailbox alone.
        """
        lowered = (sender or "").lower()
        match = re.search(r"<([^>]+)>", lowered)
        address = match.group(1) if match else lowered
        for fragment in self.excluded_senders:
            fragment = (fragment or "").strip().lower()
            if not fragment:
                continue
            if "@" in fragment:
                if address.startswith(fragment):
                    return True
            elif fragment in lowered:
                return True
        return False

    @field_validator("pan")
    @classmethod
    def _normalise_pan(cls, v: str) -> str:
        v = (v or "").strip().upper()
        if v and not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", v):
            # Don't reject - store what was given, just don't pretend it's valid.
            return v
        return v

    @field_validator("mobile")
    @classmethod
    def _digits_only(cls, v: str) -> str:
        return re.sub(r"\D", "", v or "")

    @property
    def first_name(self) -> str:
        parts = [p for p in re.split(r"\s+", self.full_name.strip()) if p]
        return parts[0] if parts else ""

    @property
    def last_name(self) -> str:
        parts = [p for p in re.split(r"\s+", self.full_name.strip()) if p]
        return parts[-1] if len(parts) > 1 else ""

    def has_password_material(self) -> bool:
        """True when there is enough here to derive at least one password."""
        return bool(self.full_name or self.date_of_birth or self.pan
                    or self.mobile or self.custom_passwords)
