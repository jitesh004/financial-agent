/* Every screen, rendered against a real ledger, twice.
 *
 * Once over the demo workspace, and once over an account with nothing in it -
 * because "works on my data" and "works on a fresh install" are different
 * claims, and the second is the one every new user makes first. A screen that
 * divides by a month count, indexes the first element of a list, or reads
 * `totals.x.y` on an empty payload passes the first and fails the second.
 *
 * These are deliberately shallow: they assert a screen paints its own heading
 * and raises nothing. What each screen DOES is asserted in its own file.
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import { createServer, renderScreen, fixture } from './harness';

import Overview from '../src/screens/Overview';
import Spending from '../src/screens/Spending';
import Budget from '../src/screens/Budget';
import Months from '../src/screens/Months';
import Forecast from '../src/screens/Forecast';
import Recurring from '../src/screens/Recurring';
import Review from '../src/screens/Review';
import Ledger from '../src/screens/Ledger';
import Position from '../src/screens/Position';
import Debt from '../src/screens/Debt';
import Credit from '../src/screens/Credit';
import Portfolio from '../src/screens/Portfolio';
import Owed from '../src/screens/Owed';
import Explore from '../src/screens/Explore';
import Agents from '../src/screens/Agents';
import Data from '../src/screens/Data';
import Rules from '../src/screens/Rules';
import Settings from '../src/screens/Settings';
import Profile from '../src/screens/Profile';

/* An empty install: every list empty, every total zero, no accounts. The
   shapes are the real ones - only the contents are emptied. */
const EMPTY_ROUTES = {
  'GET /api/accounts': [],
  'GET /api/statements': [],
  'GET /api/files': [],
  'GET /api/recurring': [],
  'GET /api/claims': [],
  'GET /api/dashboards': [],
  'GET /api/coverage': { accounts: [] },
  'GET /api/periods': {
    ...fixture('/api/periods'),
    months: [], earliest: null, latest: null,
  },
  'GET /api/dashboard': { status: 'empty', message: 'Nothing imported yet.' },
  'GET /api/workflow': {
    stages: [], counts: { files: 0, accounts: 0, transactions: 0, needs_review: 0 },
  },
};

const SCREENS = [
  ['Overview', Overview, '/', /summary|overview|nothing/i],
  ['Spending', Spending, '/spending', /spending/i],
  ['Budget', Budget, '/budget', /month/i],
  ['Months', Months, '/months', /month/i],
  ['Forecast', Forecast, '/forecast', /next|forecast|balance/i],
  ['Recurring', Recurring, '/recurring', /recurring/i],
  ['Review', Review, '/review', /review/i],
  ['Ledger', Ledger, '/ledger', /ledger/i],
  ['Position', Position, '/position', /position|owe|checked/i],
  ['Debt', Debt, '/debt', /debt|loan|card/i],
  ['Credit', Credit, '/credit', /credit/i],
  ['Portfolio', Portfolio, '/portfolio', /portfolio|own|holding/i],
  ['Owed', Owed, '/owed', /owed/i],
  ['Explore', Explore, '/explore', /explore|dashboard|widget/i],
  ['Agents', Agents, '/agents', /agent/i],
  ['Data', Data, '/data', /data|coverage|file/i],
  ['Rules', Rules, '/rules', /rule/i],
  ['Settings', Settings, '/settings', /setting|categor|display/i],
  ['Profile', Profile, '/profile', /detail|name/i],
];

/* A screen that throws inside React is reported to console.error and then
   rendered as nothing, which a "did it paint?" assertion would miss entirely.
   Failing on it is the whole point of a smoke test. */
let consoleErrors;
let spy;
beforeEach(() => {
  consoleErrors = [];
  spy = vi.spyOn(console, 'error').mockImplementation((...args) => {
    consoleErrors.push(args.map(String).join(' '));
  });
});
afterEach(() => spy?.mockRestore());

function assertNoReactErrors(name) {
  const real = consoleErrors.filter(
    (line) => /The above error|Consider adding an error boundary|Uncaught|is not a function|Cannot read/i
      .test(line));
  expect(real, `${name} logged React errors:\n${real.join('\n')}`).toEqual([]);
}

describe('every screen renders over a real ledger', () => {
  beforeEach(() => { createServer(); });

  it.each(SCREENS)('%s', async (name, Screen, route, expected) => {
    renderScreen(<Screen onImport={() => {}} />, { route });
    await waitFor(() => {
      expect(document.body.textContent).toMatch(expected);
    }, { timeout: 8000 });
    assertNoReactErrors(name);
  });
});

describe('every screen renders on a fresh install', () => {
  beforeEach(() => { createServer({ routes: EMPTY_ROUTES, transactions: [] }); });

  it.each(SCREENS)('%s', async (name, Screen, route) => {
    renderScreen(<Screen onImport={() => {}} />, { route });
    // Something has to be on screen - a heading, an empty state, a prompt to
    // import - rather than a blank page.
    await waitFor(() => {
      expect(document.body.textContent.trim().length).toBeGreaterThan(10);
    }, { timeout: 8000 });
    assertNoReactErrors(name);
  });
});

describe('every screen survives the server being down', () => {
  beforeEach(() => {
    createServer({
      routes: Object.fromEntries(
        // Every path the app knows, answered 500.
        ['/api/dashboard', '/api/accounts', '/api/transactions', '/api/periods',
          '/api/analysis', '/api/budget', '/api/recurring', '/api/claims',
          '/api/position', '/api/bureau', '/api/portfolio', '/api/agents',
          '/api/dashboards', '/api/rules', '/api/settings', '/api/profile',
          '/api/coverage', '/api/files', '/api/data/inventory', '/api/workflow',
          '/api/categories', '/api/query/schema', '/api/statements',
          '/api/bureau/reconciliation', '/api/position/mappable',
          '/api/position/snapshots', '/api/dashboards/templates']
          .map((p) => [`GET ${p}`, { status: 500, body: { detail: 'server unavailable' } }]),
      ),
    });
  });

  it.each(SCREENS)('%s', async (name, Screen, route) => {
    renderScreen(<Screen onImport={() => {}} />, { route });
    await waitFor(() => {
      expect(document.body.textContent.trim().length).toBeGreaterThan(5);
    }, { timeout: 8000 });
    assertNoReactErrors(name);
  });
});
