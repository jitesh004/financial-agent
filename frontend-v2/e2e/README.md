# Running the app, in a browser

Two suites cover the v2 front-end, and they check different things.

`npm test` (vitest, `../test`) drives every screen against a fake server whose
answers were captured from the real API. It is fast, it runs anywhere, and it
proves each screen does the right thing **given an answer**.

`npm run test:e2e` (this directory) drives the real app in a real browser
against a real FastAPI server and a real database. It proves the two halves
**fit**: that the query a screen builds is one the API understands, that what
comes back is the shape it reads, and that a click here changes a figure there.

Neither replaces the other. The ledger's search box passed every unit test it
had while doing nothing at all, because both sides of the contract were mocked
by the same assumption.

## What you need

* A **throwaway** PostgreSQL database, owned by an ordinary (non-superuser)
  role. Both suites write to it freely.
* A browser. Playwright is a dev dependency; `FA_CHROME` points at a Chromium
  binary if Playwright's own is not installed.

## The main suite

```sh
export FA_DATABASE_URL=postgresql://financial_agent:…@localhost:5432/fa_e2e
export FA_DATA_DIR=/tmp/fa-e2e

# a workspace with the generated demo ledger in it; prints FA_SESSION
python backend/tools/e2e_workspace.py --demo

python -m uvicorn app.main:app --port 8078 --app-dir backend &
npm --prefix frontend-v2 run dev &

FA_SESSION=… npm --prefix frontend-v2 run test:e2e
```

It walks every screen, then exercises the ledger's filters, search, sorting and
row explanations; the drill-down behind a headline figure; the period control;
the command palette; recurring series; corrections and confirmations on
Position; categories and display preferences in Settings; the review queue;
dashboards; the rule tester; marking a purchase as somebody else's and settling
it; the file registry; and that no destructive action on the Data screen fires
on a single click.

Anything the browser itself complains about — an uncaught error, an error
logged to the console, a failed request, any 4xx or 5xx from the API — fails
the run, wherever it happened.

## The import suite

```sh
python backend/tools/generate_samples.py              # once
python backend/tools/e2e_workspace.py --empty         # prints FA_SESSION

FA_SESSION=… npm --prefix frontend-v2 run test:e2e:import
```

This is the whole first-run path, end to end: an empty install, the wizard,
three real statement files in three different formats, the parse, the review
and the build — finishing with transactions actually in the ledger and an
overview that is no longer empty. It needs a workspace with nothing in it, and
says so plainly rather than skipping if it finds one that is not empty.

## Settings

| Variable            | Default                        |
|---------------------|--------------------------------|
| `FA_SESSION`        | required — from `e2e_workspace.py` |
| `FA_BASE_URL`       | `http://127.0.0.1:5173`        |
| `FA_SESSION_COOKIE` | `fa_session`                   |
| `FA_SAMPLES`        | `data/samples`                 |
| `FA_CHROME`         | Playwright's own Chromium      |

Sign-in goes through Google, which a headless run cannot do and should not have
to, so `e2e_workspace.py` mints a session directly and the suite presents it as
a cookie. That is the only thing either suite fakes.
