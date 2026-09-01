"""Every hardcoded rule this app applies, and where each one lives.

READ THIS FIRST if you are looking for a rule.

There are two "one places", for two different things, and knowing which you
want saves a search:

  1. THIS PACKAGE holds knowledge that MORE THAN ONE module needs. Fifteen
     modules import from it. Anything here was duplicated somewhere before it
     moved, and the duplication had usually already drifted.

  2. `api/rules_routes.py` is the single INDEX over every rule family in the
     app, whether it lives here or next to its reader. It is what the Rules
     tab renders, and it is the fastest way to see the whole surface at once
     - `GET /api/rules`, or open the tab.

A rule that only ONE reader uses stays WITH that reader. Moving it here would
separate it from the code that applies it and from the comment explaining
which real document forced it, and those comments are the actual
specification. Distance from the thing being explained is its own bug.

WHAT IS IN HERE
---------------
    institutions.py   Every bank, card, broker, bureau and wallet - one record
                      each. Name fragments, which scans look for them, PDF
                      password format, bureau key, portfolio layout. Fourteen
                      hand-maintained lists across four modules collapse to
                      this. ADD A BANK HERE AND NOWHERE ELSE.
    formats.py        The shapes documents are written in: months, payment
                      rails, blank-figure tokens, masked account numbers,
                      card-bill wordings, and the one money-rounding rule.
    passwords.py      The five PDF password formats and what each needs from
                      the user's profile.
    directions.py     The five signals that decide whether a row is money in
                      or money out, ranked, with the sentence each shows.
    thresholds.py     Every tunable number, IMPORTING the live constant rather
                      than repeating it, paired with the reason it is that
                      number.

WHERE THE REST LIVES
--------------------
    categorize/rules.py        the 51 category rules, in the order they run
    ingestion/txn_email.py     the 12 per-issuer alert templates
    ingestion/gmail_source.py  the email rejection rules and scan subjects
    ingestion/bureau.py        bureau field labels and boundaries
    ingestion/portfolio.py     holdings column hints, trade markers, ISIN
    normalize/column_map.py    column aliases, and what makes a row a header
    normalize/metadata.py      card products, account types, and the
                               account-number precedence
    normalize/normalizer.py    table ranking, balance markers, direction
    analytics/periods.py       which month a transaction counts in
    analytics/recurring.py     cadences and what makes a charge recurring
    reconcile/                 transfers, settlements, bureau matching
    llm/client.py              what is stripped before a model sees anything

THE RULE FOR ADDING ONE
-----------------------
Does a second module need it? Put it here. Otherwise put it beside its reader,
with a comment naming the document that made it necessary - and add it to
`api/rules_routes.py` so it appears on the Rules tab. A rule the app applies
and never shows is one the user cannot check, which is the fault this whole
package exists to remove. `tests/test_rules.py` asserts every section the API
publishes is actually rendered.
"""
