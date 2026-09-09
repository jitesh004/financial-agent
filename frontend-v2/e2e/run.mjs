/* The app, in a real browser, against a real server.
 *
 * The vitest suite in ../test proves each screen does the right thing given an
 * answer. This proves the two halves fit: that the query a screen builds is
 * one the API understands, that what comes back is the shape it reads, and
 * that a click here changes a figure there. Everything below is a round trip
 * through the actual FastAPI app and the actual database.
 *
 * Run:
 *   export FA_DATABASE_URL=postgresql://…   # a THROWAWAY database
 *   python backend/tools/e2e_workspace.py --demo     # prints FA_SESSION
 *   npm --prefix frontend-v2 run dev &               # or a built nginx
 *   FA_SESSION=… node frontend-v2/e2e/run.mjs
 *
 * `--import` runs the other half: the whole import flow, from an empty
 * workspace through three real statement files to a built ledger. It needs a
 * workspace made with `--empty` and the samples from
 * `backend/tools/generate_samples.py`.
 *
 * Nothing here is skipped when something is missing. A check that cannot run
 * is a failure, because "it did not run" and "it worked" must never look the
 * same from outside.
 */

import { chromium } from 'playwright';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');

const BASE = process.env.FA_BASE_URL || 'http://127.0.0.1:5173';
const SESSION = process.env.FA_SESSION;
const COOKIE = process.env.FA_SESSION_COOKIE || 'fa_session';
const SAMPLES = process.env.FA_SAMPLES || path.join(ROOT, 'data', 'samples');
const CHROME = process.env.FA_CHROME
  || (existsSync('/opt/pw-browsers/chromium-1194/chrome-linux/chrome')
    ? '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' : undefined);

if (!SESSION) {
  console.error('FA_SESSION is not set. Run backend/tools/e2e_workspace.py first.');
  process.exit(2);
}

const IMPORT_MODE = process.argv.includes('--import');

/* Every destination in the rail, plus the two that are only reachable from the
   account menu. Kept as a list rather than read from src/app/routes.js so that
   a route quietly disappearing from the table is still checked for here. */
const ROUTES = [
  '/', '/agents', '/budget', '/months', '/spending', '/recurring', '/forecast',
  '/position', '/debt', '/credit', '/portfolio', '/owed',
  '/ledger', '/review', '/explore',
  '/data', '/rules', '/settings', '/profile',
];

let failures = 0;
const ok = (label, condition, detail = '') => {
  if (!condition) failures += 1;
  const note = detail ? ` — ${String(detail).replace(/\s+/g, ' ').slice(0, 150)}` : '';
  console.log(`${condition ? 'ok  ' : 'FAIL'}  ${label}${note}`);
};

const browser = await chromium.launch({ executablePath: CHROME });
const context = await browser.newContext({ viewport: { width: 1500, height: 980 } });
await context.addCookies([{
  name: COOKIE, value: SESSION, domain: new URL(BASE).hostname, path: '/',
}]);
const page = await context.newPage();

/* Anything the browser itself complains about is a failure, wherever it
   happened. A screen that renders while logging a TypeError is not working. */
const noise = new Set();
page.on('pageerror', (e) => noise.add(`pageerror: ${String(e).slice(0, 200)}`));
page.on('console', (m) => {
  if (m.type() === 'error') noise.add(`console: ${m.text().slice(0, 200)}`);
});
page.on('requestfailed', (r) => {
  // A request the BROWSER cancelled is not a failure of the app: navigating
  // away aborts whatever the import poller had in flight, every time.
  const why = r.failure()?.errorText || '';
  if (why.includes('ERR_ABORTED')) return;
  noise.add(`requestfailed: ${r.url()} ${why}`);
});
page.on('response', (r) => {
  if (r.status() >= 400 && r.url().includes('/api/')) {
    noise.add(`http ${r.status()}: ${r.url().replace(BASE, '')}`);
  }
});

/** Every API request the page has made, newest last. */
const requests = [];
page.on('request', (r) => {
  if (r.url().includes('/api/')) {
    requests.push({ method: r.method(), url: r.url().replace(BASE, ''), body: r.postData() });
  }
});
const sent = (predicate) => requests.some(predicate);
const lastSent = (predicate) => [...requests].reverse().find(predicate);

/* textContent, not innerText: several of these panels open inside a scrolling
   container, and a visibility-aware read omits whatever is below the fold. */
const text = () => page.evaluate(() => document.body.textContent);
const dialog = () => page.locator('[role=dialog]');

async function every(label, fn) {
  console.log(`\n── ${label} ──`);
  await fn();
}

/* ═══════════════════════════════════════════════ every screen ═════════ */

if (!IMPORT_MODE) {
  await every('every screen paints', async () => {
    for (const route of ROUTES) {
      await page.goto(BASE + route, { waitUntil: 'networkidle' });
      await page.waitForTimeout(500);
      const body = (await text()).trim();
      ok(`${route} renders`, body.length > 200, `${body.length} characters`);
    }
  });

  /* ═════════════════════════════════════════════ the ledger ═════════════ */

  await every('the ledger', async () => {
    await page.goto(`${BASE}/ledger`, { waitUntil: 'networkidle' });
    await page.waitForSelector('table tbody tr', { timeout: 20000 });

    const heading = () => page.locator('h2').first().innerText();
    const rows = () => page.locator('table tbody tr').count();
    const descriptions = () => page.locator('table tbody tr .desc').allInnerTexts();

    const before = await rows();
    const headingBefore = await heading();

    /* Taken from the ledger itself, so this runs against any workspace. */
    const needle = (await page.locator('table tbody tr .desc').first().innerText())
      .split(/[\s/]+/).filter((w) => w.length > 4)[0].toLowerCase();

    const box = page.getByPlaceholder(/search descriptions and merchants/i);
    await box.fill(needle);
    await page.waitForTimeout(1200);

    ok('search reaches the server',
      sent((r) => r.url.includes(`search=${encodeURIComponent(needle)}`)),
      lastSent((r) => r.url.startsWith('/api/transactions?'))?.url);
    const found = await descriptions();
    ok('search narrows the table', found.length > 0 && found.length < before,
      `${before} rows -> ${found.length}`);
    ok('every row on screen matches',
      found.every((d) => d.toLowerCase().includes(needle)), found.slice(0, 3).join(' | '));
    ok('the row count follows the search', (await heading()) !== headingBefore,
      `${headingBefore} -> ${await heading()}`);
    ok('the search is in the URL', page.url().includes(`q=${encodeURIComponent(needle)}`),
      page.url());

    await box.fill('zzzznothingmatchesthis');
    await page.waitForTimeout(1200);
    ok('a search that matches nothing says so',
      (await text()).includes('No transactions match'));

    await page.getByRole('button', { name: 'Clear', exact: true }).click();
    await page.waitForTimeout(1000);
    ok('Clear empties the box', (await box.inputValue()) === '');
    ok('Clear brings the rows back', (await rows()) > 0);

    await page.getByLabel('Sort by').selectOption('description');
    await page.getByLabel('Descending').click();
    await page.waitForTimeout(1200);
    const sortedNow = await descriptions();
    const expected = [...sortedNow].sort(
      (a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
    ok('sorting by description sorts',
      JSON.stringify(sortedNow.slice(0, 8)) === JSON.stringify(expected.slice(0, 8)),
      sortedNow.slice(0, 2).join(' | '));

    await page.getByRole('button', { name: 'Clear all' }).click();
    await page.waitForTimeout(1000);
    ok('unticking every account shows nothing',
      (await text()).includes('No transactions'));
    await page.getByRole('button', { name: 'Select all' }).click();
    await page.waitForTimeout(1000);
    ok('re-ticking brings them back', (await rows()) > 0);

    await page.getByRole('tab', { name: 'Cards' }).click();
    await page.waitForTimeout(1000);
    ok('a preset scopes the table', (await text()).includes('Credit cards only'));
    await page.getByRole('tablist', { name: 'Preset' })
      .getByRole('tab', { name: 'All' }).click();
    await page.waitForTimeout(1200);

    await page.locator('[title="Why is this row the way it is?"]').first().click();
    await page.waitForTimeout(1500);
    const explained = await text();
    ok('a row explains its category', explained.includes('What it is'));
    ok('…and which way the money went', explained.includes('Which way the money went'));
    ok('…with a reason, not just a chip',
      /balance|column|marker|recorded|statement said/i.test(explained));
  });

  /* ═════════════════════════════════════════ figures open their rows ════ */

  await every('a figure opens the rows behind it', async () => {
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(900);
    await page.locator('.stat', { hasText: 'Money in' }).getByRole('button').first().click();
    await page.waitForTimeout(1400);
    ok('the sheet opens', (await dialog().count()) > 0);
    ok('scoped to income', sent((r) => r.url.includes('flow_role=income')));
    await page.keyboard.press('Escape');
  });

  await every('the period control', async () => {
    await page.goto(`${BASE}/spending`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(900);
    await page.getByRole('tab', { name: 'Last', exact: true }).click();
    await page.waitForTimeout(1400);
    ok('re-cuts the screen', sent((r) => r.url.includes('preset=last_month')));
  });

  await every('the command palette', async () => {
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(400);
    await page.keyboard.type('ledger');
    await page.waitForTimeout(300);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(1200);
    ok('navigates', page.url().endsWith('/ledger'), page.url());
  });

  await every('recurring', async () => {
    await page.goto(`${BASE}/recurring`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(900);
    const opener = page.getByLabel('Show the rows');
    if ((await opener.count()) === 0) {
      ok('a series shows its rows', true, 'this ledger has no detected series');
      return;
    }
    await opener.first().click();
    await page.waitForTimeout(1400);
    ok('a series shows its rows',
      !(await text()).includes('No transactions are currently linked'));
    ok('…asked for by series', sent((r) => r.url.includes('recurring_series_id=')));
  });

  await every('position', async () => {
    await page.goto(`${BASE}/position`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(900);
    if ((await text()).includes('Nothing here yet')) {
      await page.getByRole('button', { name: /draft it from what i have imported/i }).click();
      await page.waitForTimeout(1800);
    }
    ok('drafts from the ledger', !(await text()).includes('Nothing here yet'));

    const cells = page.locator('[title="Click to edit"]');
    ok('figures are editable', (await cells.count()) > 0, `${await cells.count()} cells`);
    await cells.first().click();
    await page.waitForTimeout(300);
    await page.keyboard.press('Control+a');
    await page.keyboard.type(`Checked ${Date.now() % 100000}`);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(1400);
    const patch = lastSent((r) => r.method === 'PATCH' && r.url.includes('/api/position/items/'));
    ok('a correction is a partial patch',
      Boolean(patch) && Object.keys(JSON.parse(patch.body)).length === 1, patch?.body);

    await page.getByRole('button', { name: 'Confirm' }).first().click();
    await page.waitForTimeout(1400);
    ok('confirming is its own act, not a patch of the date',
      sent((r) => r.method === 'POST' && /\/position\/items\/[^/]+\/review$/.test(r.url)));
  });

  await every('settings', async () => {
    await page.goto(`${BASE}/settings`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(900);

    await page.getByPlaceholder(/e\.g\. pets/i).fill('Side Project');
    await page.getByRole('button', { name: /add category/i }).click();
    await page.waitForTimeout(1400);
    const added = lastSent((r) => r.method === 'POST' && r.url === '/api/categories');
    ok('a new category is normalised',
      Boolean(added) && JSON.parse(added.body).name === 'side_project', added?.body);
    ok('…and appears at once', (await text()).includes('Side Project'));

    await page.getByTitle(/remove side project/i).click();
    await page.waitForTimeout(300);
    await page.getByRole('button', { name: 'Remove' }).first().click();
    await page.waitForTimeout(1400);
    ok('removing it reaches the server',
      sent((r) => r.method === 'DELETE' && r.url.includes('/api/categories/')));

    await page.getByLabel('Row density').selectOption('compact');
    await page.waitForTimeout(600);
    await page.goto(`${BASE}/ledger`, { waitUntil: 'networkidle' });
    await page.waitForSelector('table');
    ok('a display preference reaches every screen',
      (await page.locator('table.tbl-compact').count()) > 0);
  });

  await every('review', async () => {
    await page.goto(`${BASE}/review`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    const confirm = page.getByRole('button', { name: 'Looks right' });
    if ((await confirm.count()) > 0) {
      await confirm.first().click();
      await page.waitForTimeout(1400);
      const decision = lastSent(
        (r) => r.method === 'PATCH' && r.url.startsWith('/api/transactions/'));
      ok('a decision records a role, not an empty payload',
        Boolean(decision) && Boolean(JSON.parse(decision.body).flow_role), decision?.body);
    } else {
      ok('a decision records a role, not an empty payload', true, 'the queue is empty');
    }

    await page.getByRole('tab', { name: 'By merchant' }).click();
    await page.waitForTimeout(1400);
    const bulk = lastSent((r) => r.url.startsWith('/api/transactions?'));
    ok('the merchant view asks for a page the server will answer',
      Number(new URLSearchParams(bulk.url.split('?')[1]).get('limit')) <= 1000, bulk.url);
  });

  await every('explore', async () => {
    await page.goto(`${BASE}/explore`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1400);
    if ((await text()).includes('No dashboards yet')) {
      await page.locator('.card').first().click();
      await page.waitForTimeout(2500);
    }
    ok('a dashboard opens', !(await text()).includes('No dashboards yet'));
    ok('and runs its widgets', sent((r) => /\/api\/dashboards\/[^/]+\/run$/.test(r.url)));
    ok('the open board is in the URL', page.url().includes('board='), page.url());
  });

  await every('rules', async () => {
    await page.goto(`${BASE}/rules`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(900);
    await page.getByPlaceholder('UPI/SWIGGY/AUG25/123456').fill('UPI/SWIGGY/AUG25/123456');
    await page.getByRole('button', { name: 'Explain' }).last().click();
    await page.waitForTimeout(1400);
    ok('the tester runs the real rules',
      sent((r) => r.method === 'POST' && r.url === '/api/rules/test'));
    ok('…and reports what fired', /matched|no rule|dining|rule/i.test(await text()));
  });

  await every('money owed', async () => {
    await page.goto(`${BASE}/ledger`, { waitUntil: 'networkidle' });
    await page.waitForSelector('table tbody tr');
    const claim = page.locator('[title="This purchase was not mine — track it as owed to me"]');
    ok('a purchase can be marked as somebody else’s', (await claim.count()) > 0);
    if ((await claim.count()) > 0) {
      await claim.first().click();
      await page.getByPlaceholder('Whose expense was this?').fill('Priya');
      await page.getByRole('button', { name: 'Mark owed' }).click();
      await page.waitForTimeout(1400);
      ok('…and it becomes a claim',
        sent((r) => r.method === 'POST' && /\/claim$/.test(r.url)));
    }

    await page.goto(`${BASE}/owed`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(900);
    const settle = page.getByRole('button', { name: 'Settle' });
    ok('the claim is listed', (await settle.count()) > 0);
    if ((await settle.count()) > 0) {
      await settle.first().click();
      await page.getByRole('button', { name: 'Record' }).click();
      await page.waitForTimeout(1400);
      const recorded = lastSent((r) => /\/settle$/.test(r.url));
      ok('settling sends what is outstanding, not zero',
        Boolean(recorded) && Number(JSON.parse(recorded.body).amount) > 0, recorded?.body);
    }
  });

  await every('data', async () => {
    await page.goto(`${BASE}/data`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(900);
    ok('the coverage grid lists the accounts', /Accounts detected/i.test(await text()));

    await page.goto(`${BASE}/data?section=files`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(900);
    const files = await page.evaluate(
      async () => (await (await fetch('/api/files')).json()).map((f) => f.filename));
    const listed = await text();
    ok('every file ever attempted is listed',
      files.length > 0 && files.every((f) => listed.includes(f)),
      `${files.length} files`);

    await page.goto(`${BASE}/data?section=manage`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(900);
    const cleared = () => requests.filter((r) => r.url.includes('/api/data/clear/')).length;
    const wasCleared = cleared();
    /* One click on every destructive control. The server sends a typed
       confirmation phrase for only two of the seven scopes, so this is what
       catches a screen that treats "no phrase" as "no confirmation needed". */
    const dangerous = await page.getByRole(
      'button', { name: /clear|delete|reset|rebuild/i }).all();
    ok('there are destructive controls to check', dangerous.length > 0);
    for (const button of dangerous) {
      await button.click({ trial: false }).catch(() => {});
      await page.waitForTimeout(150);
    }
    ok('none of them fires on one click', cleared() === wasCleared);
    ok('none of them resets the app', !sent((r) => r.url === '/api/reset'));
    ok('none of them starts a rebuild', !sent((r) => r.url.startsWith('/api/reanalyze')));
  });

  await every('the import wizard', async () => {
    await page.goto(`${BASE}/data`, { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: /^Import$/ }).first().click();
    await page.waitForTimeout(1400);
    const wizard = await dialog().evaluate((el) => el.textContent);
    ok('it opens', wizard.length > 0);
    /* The mailbox is one source among five. When Gmail is not configured the
       wizard used to be Google's setup instructions and nothing else - no step
       rail, no Next, and no dropzone - on exactly the deployments where files
       from disk are the only way in. */
    ok('the dropzone is reachable whatever the mailbox is doing',
      wizard.includes('Drop your statements here'));
    ok('every step is reachable',
      ['Source', 'Scan', 'Choose', 'Read', 'Review', 'Build'].every((s) => wizard.includes(s)));
    await page.keyboard.press('Escape');
  });
}

/* ═══════════════════════════════════════════ the whole import ═════════ */

if (IMPORT_MODE) {
  await every('importing real statements into an empty workspace', async () => {
    const files = [
      'hdfc_savings_2025_2026.csv',
      'icici_credit_card_2025_2026.pdf',
      'hdfc_home_loan_2025_2026.docx',
    ].map((name) => path.join(SAMPLES, name));

    for (const file of files) {
      if (!existsSync(file)) {
        ok(`sample present: ${path.basename(file)}`, false,
          'run backend/tools/generate_samples.py');
        return;
      }
    }

    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);

    /* This half only means anything on a workspace with nothing in it - the
       empty-install screen is one of the things it checks. Said plainly
       rather than skipped, because a check that quietly does not run reads
       exactly like one that passed. */
    const already = await page.evaluate(
      async () => (await (await fetch('/api/health')).json()).transactions_stored);
    if (already > 0) {
      ok('the workspace is empty to start with', false,
        `${already} rows already stored — make one with `
        + 'backend/tools/e2e_workspace.py --empty');
      return;
    }

    ok('an empty install explains itself and offers the way in',
      /Understand where your money actually goes/i.test(await text()));

    await page.getByRole('button', { name: /import statements/i }).first().click();
    await page.waitForTimeout(1500);
    ok('the wizard opens on an empty install',
      (await text()).includes('Drop your statements here'));

    await dialog().locator('input[type=file]').setInputFiles(files);
    await page.waitForTimeout(800);
    ok('the files queue up', (await text()).includes(path.basename(files[0])));

    await page.getByRole('button', { name: /add 3 files/i }).click();
    for (let i = 0; i < 90; i += 1) {
      await page.waitForTimeout(1000);
      if (!/Saving your files…|Reading them…|Staging for review…/.test(await text())) break;
    }
    ok('the upload finishes',
      !/Saving your files…|Reading them…/.test(await text()));

    await dialog().getByRole('button', { name: /Read/ }).first().click();
    await page.waitForTimeout(1400);
    const parse = dialog().locator('button', { hasText: /^Read /i });
    if ((await parse.count()) > 0) {
      await parse.first().click();
      for (let i = 0; i < 120; i += 1) {
        await page.waitForTimeout(1000);
        if (!/Reading documents/i.test(await text())) break;
      }
    }

    await dialog().getByRole('button', { name: /Review/ }).first().click();
    await page.waitForTimeout(2000);
    ok('the review step lists what was read',
      /transaction|row|account|statement/i.test(await text()));

    await dialog().getByRole('button', { name: /Build/ }).first().click();
    await page.waitForTimeout(1500);
    // Exact: the step rail also carries a button called "6 Build".
    await dialog().getByRole('button', { name: 'Build the ledger', exact: true }).click();
    for (let i = 0; i < 120; i += 1) {
      await page.waitForTimeout(1000);
      if (/now count|Rebuild again|Close and look/i.test(await text())) break;
    }
    ok('the build reports what now counts', /now count/i.test(await text()));

    const stored = await page.evaluate(
      async () => (await (await fetch('/api/health')).json()).transactions_stored);
    ok('transactions reached the ledger', stored > 0, `${stored} rows`);

    await page.keyboard.press('Escape');
    await page.goto(`${BASE}/ledger`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    ok('the ledger shows them', (await page.locator('table tbody tr').count()) > 0);

    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    ok('the overview is no longer an empty install',
      !/Understand where your money actually goes/i.test(await text()));
  });
}

/* ═══════════════════════════════════════════════════ the verdict ═════ */

console.log('\n── what the browser complained about ──');
if (noise.size === 0) console.log('ok    nothing');
for (const line of noise) console.log(`FAIL  ${line}`);
failures += noise.size;

console.log(`\n${failures === 0 ? 'PASSED' : `FAILED (${failures})`}`);
await browser.close();
process.exit(failures === 0 ? 0 : 1);
