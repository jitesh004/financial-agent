"""The PDF password formats Indian issuers use, and what each one needs.

A format is named once here and referred to by that name from
`rules.institutions`. Previously the label and its explanation were repeated on
every issuer that used it - twelve copies of "First 4 letters of name + DDMM",
three of which had drifted into different wordings for the same rule.

`profile_needs` is what makes the UI able to say "we cannot open this one, you
have not entered your PAN" before the user waits for a download and a failed
parse. It used to be inferred by looking for substrings inside the label
("PAN" in label), which meant renaming a label silently changed which profile
fields the app thought it needed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PasswordFormat:
    #: Short label, shown in the UI and used as the key from an Institution.
    label: str
    #: What the user has to know, in their words.
    explanation: str
    #: Profile fields required to generate a candidate for this format.
    #: Names match `models.profile.UserProfile` attributes.
    needs: tuple[str, ...]
    #: True when the value is one only the user has - not derivable from any
    #: profile field, and not guessable within a bounded candidate set. The
    #: app can open such a file only if the user has entered the password
    #: themselves, so saying so up front is the whole of the help available.
    user_supplied: bool = False


FORMATS: tuple[PasswordFormat, ...] = (
    PasswordFormat(
        "Name(4) + DDMM",
        "First 4 letters of your name + date of birth as DDMM",
        ("full_name", "date_of_birth")),
    PasswordFormat(
        "DDMMYYYY",
        "Date of birth as DDMMYYYY",
        ("date_of_birth",)),
    PasswordFormat(
        "PAN",
        "Your PAN in uppercase",
        ("pan",)),
    PasswordFormat(
        "Mobile(10)",
        "Your registered 10-digit mobile number",
        ("mobile",)),
    PasswordFormat(
        "Card(4) + DDMM",
        "Last 4 digits of the card + date of birth as DDMM",
        ("date_of_birth",)),
    PasswordFormat(
        "PRAN",
        "Your 12-digit PRAN for that NPS account - add it under extra "
        "passwords in your profile; it cannot be worked out from anything "
        "else you have entered",
        (), user_supplied=True),
)

BY_LABEL: dict[str, PasswordFormat] = {f.label: f for f in FORMATS}

#: Shown when no issuer matches. The full candidate set is still tried - the
#: hint tells the user what to expect, it does not restrict what is attempted.
UNKNOWN = PasswordFormat(
    "Unknown", "Format not documented here; all known formats are tried", ())
