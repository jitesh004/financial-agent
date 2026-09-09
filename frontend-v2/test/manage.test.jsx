/* Review, Explore, Agents, Data, Rules, Settings, Profile.
 *
 * The screens that change something. Their common failure mode is a write that
 * looks like it worked - a button that fires a request the server discards, a
 * list that never re-reads, a limit the screen believes and the server does
 * not - so the assertions here are about the request that goes out and about
 * what the screen re-reads afterwards.
 */

import React from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import Review from '../src/screens/Review';
import Explore from '../src/screens/Explore';
import Agents from '../src/screens/Agents';
import Data from '../src/screens/Data';
import Rules from '../src/screens/Rules';
import Settings from '../src/screens/Settings';
import Profile from '../src/screens/Profile';
import { createServer, renderScreen, fixture, allTransactions } from './harness';

let server;
beforeEach(() => { server = createServer(); });

const params = (call) => new URLSearchParams(call?.search || '');

/* A ledger where a few rows need review, so the queue has something in it. */
function withReviewQueue(n = 6) {
  const rows = allTransactions().map((t, i) => (i < n
    ? { ...t, needs_review: true, review_reason: 'Could be income or a repayment' }
    : { ...t, needs_review: false }));
  return createServer({ transactions: rows });
}

describe('Review', () => {
  it('asks only for the rows that need review', async () => {
    server = withReviewQueue();
    renderScreen(<Review />, { route: '/review' });
    await waitFor(() => {
      const call = server.lastCall('/api/transactions');
      expect(params(call).get('needs_review')).toBe('true');
    }, { timeout: 8000 });
  });

  it('records "looks right" as the decision it means, not an empty payload', async () => {
    const user = userEvent.setup();
    server = withReviewQueue();
    renderScreen(<Review />, { route: '/review' });

    const ok = await screen.findAllByRole('button', { name: 'Looks right' }, { timeout: 8000 });
    await user.click(ok[0]);

    await waitFor(() => {
      const call = [...server.calls].reverse().find(
        (c) => c.method === 'PATCH' && c.path.startsWith('/api/transactions/'));
      expect(call).toBeTruthy();
      // `TransactionUpdateReq` has no `needs_review` field: a payload of only
      // that matches nothing and round-trips to an empty dict, so the row
      // never leaves the queue. Confirming the role IS the decision.
      expect(call.body.flow_role).toBeTruthy();
    }, { timeout: 8000 });
  });

  it('records a different answer when one is chosen', async () => {
    const user = userEvent.setup();
    server = withReviewQueue();
    renderScreen(<Review />, { route: '/review' });

    const rows = await screen.findAllByRole('row', {}, { timeout: 8000 });
    const withSelect = rows.find((r) => within(r).queryByRole('combobox'));
    await user.selectOptions(within(withSelect).getByRole('combobox'), 'refund');

    await waitFor(() => {
      const call = [...server.calls].reverse().find(
        (c) => c.method === 'PATCH' && c.path.startsWith('/api/transactions/'));
      expect(call.body.flow_role).toBe('refund');
    }, { timeout: 8000 });
  });

  it('re-reads the queue after a decision, so it empties as it is worked', async () => {
    const user = userEvent.setup();
    server = withReviewQueue();
    renderScreen(<Review />, { route: '/review' });

    const ok = await screen.findAllByRole('button', { name: 'Looks right' }, { timeout: 8000 });
    const before = server.callsTo('/api/transactions', 'GET').length;
    await user.click(ok[0]);

    await waitFor(() => {
      expect(server.callsTo('/api/transactions', 'GET').length).toBeGreaterThan(before);
    }, { timeout: 8000 });
    await waitFor(() => {
      expect(screen.queryAllByRole('button', { name: 'Looks right' }).length).toBeLessThan(
        ok.length);
    }, { timeout: 8000 });
  });

  it('asks for no more rows than the server will return', async () => {
    const user = userEvent.setup();
    server = withReviewQueue();
    renderScreen(<Review />, { route: '/review' });

    await user.click(await screen.findByRole('tab', { name: 'By merchant' }, { timeout: 8000 }));
    await waitFor(() => {
      const call = server.lastCall('/api/transactions');
      // Asking for more than the cap is answered silently with the cap, and a
      // screen that believed its own number reported "showing the first 2000"
      // over a thousand rows.
      expect(Number(params(call).get('limit'))).toBeLessThanOrEqual(1000);
    }, { timeout: 8000 });
  });

  it('categorises a whole merchant in one request', async () => {
    const user = userEvent.setup();
    server = withReviewQueue();
    renderScreen(<Review />, { route: '/review?mode=bulk' });

    // The demo ledger has nothing uncategorised, which is the default scope.
    await user.click(await screen.findByRole('tab', { name: 'Everything' }, { timeout: 8000 }));

    const assign = await waitFor(() => {
      const found = screen.getAllByRole('combobox').find(
        (el) => /categorise all/i.test(el.querySelector('option')?.textContent || ''));
      expect(found).toBeTruthy();
      return found;
    }, { timeout: 8000 });
    await user.selectOptions(assign, 'groceries');

    await waitFor(() => {
      const call = server.lastCall('/api/transactions/bulk', 'PATCH');
      expect(call).toBeTruthy();
      expect(call.body.category).toBe('groceries');
      expect(call.body.txn_ids.length).toBeGreaterThan(0);
    }, { timeout: 8000 });
  });

  it('keeps the chosen mode in the URL', async () => {
    const user = userEvent.setup();
    server = withReviewQueue();
    renderScreen(<Review />, { route: '/review' });

    await user.click(await screen.findByRole('tab', { name: 'By merchant' }, { timeout: 8000 }));
    await waitFor(() => {
      expect(new URLSearchParams(window.location.search).get('mode')).toBe('bulk');
    }, { timeout: 8000 });
  });
});

describe('Explore', () => {
  it('opens the saved board and runs it', async () => {
    renderScreen(<Explore />, { route: '/explore' });
    await waitFor(() => {
      expect(server.calls.some((c) => /^\/api\/dashboards\/[^/]+\/run$/.test(c.path)))
        .toBe(true);
    }, { timeout: 8000 });
  });

  it('remembers which board is open in the URL', async () => {
    renderScreen(<Explore />, { route: '/explore' });
    await waitFor(() => {
      expect(new URLSearchParams(window.location.search).get('board')).toBeTruthy();
    }, { timeout: 8000 });
  });

  it('builds a board from a template when there are none', async () => {
    const user = userEvent.setup();
    server = createServer({ routes: { 'GET /api/dashboards': [] } });
    renderScreen(<Explore />, { route: '/explore' });

    const first = fixture('/api/dashboards/templates')[0];
    await user.click(await screen.findByText(first.name, {}, { timeout: 8000 }));

    await waitFor(() => {
      const call = server.lastCall('/api/dashboards', 'POST');
      expect(call).toBeTruthy();
      expect(call.body.template).toBe(first.key);
    }, { timeout: 8000 });
  });
});

describe('Agents', () => {
  it('lists the agents and what each one is for', async () => {
    renderScreen(<Agents />, { route: '/agents' });
    const first = fixture('/api/agents').agents[0];
    expect(await screen.findByText(first.name, {}, { timeout: 8000 })).toBeTruthy();
  });

  it('runs an agent as a job and carries the question typed with it', async () => {
    const user = userEvent.setup();
    server = createServer({
      routes: {
        // Without a model configured the catalogue blocks every run, and a
        // disabled button is not what this test is about.
        'GET /api/agents': { ...fixture('/api/agents'), model_available: true },
        'POST /api/agents/spending/run': { job_id: 'agent-1' },
        'GET /api/jobs/agent-1': {
          id: 'agent-1', kind: 'agent', active: false, status: 'complete',
          progress: 1, result: { run_id: 'run-1' }, steps: [],
        },
        'GET /api/agents/runs/run-1': {
          id: 'run-1', agent: fixture('/api/agents').agents[0].key,
          question: 'Where did the money go?', answer: 'It went on rent.',
          findings: [], created_at: '2026-09-01T00:00:00Z', steps: [],
        },
      },
    });
    renderScreen(<Agents />, { route: '/agents' });

    const box = await screen.findAllByPlaceholderText(
      /something specific to focus on/i, {}, { timeout: 8000 });
    await user.type(box[0], 'rent');
    const runButtons = screen.getAllByRole('button', { name: /^run$/i });
    await user.click(runButtons[0]);

    await waitFor(() => {
      const call = [...server.calls].reverse().find(
        (c) => c.method === 'POST' && /^\/api\/agents\/[^/]+\/run$/.test(c.path));
      expect(call).toBeTruthy();
      expect(call.body.question).toBe('rent');
    }, { timeout: 8000 });
  });
});

describe('Data', () => {
  it('shows the coverage grid, one cell per account per month', async () => {
    renderScreen(<Data />, { route: '/data' });
    await waitFor(() => {
      expect(server.lastCall('/api/coverage')).toBeTruthy();
    }, { timeout: 8000 });
    const account = fixture('/api/coverage').accounts[0];
    expect(await screen.findByText(
      account.display_name, {}, { timeout: 8000 })).toBeTruthy();
  });

  it('lists every file ever attempted, whatever happened to it', async () => {
    renderScreen(<Data />, { route: '/data?section=files' });
    await waitFor(() => {
      expect(server.lastCall('/api/files')).toBeTruthy();
    }, { timeout: 8000 });
    const file = fixture('/api/files')[0];
    expect(await screen.findByText(
      new RegExp(file.filename.slice(0, 12), 'i'), {}, { timeout: 8000 })).toBeTruthy();
  });

  it('never clears anything without an explicit confirmation', async () => {
    const user = userEvent.setup();
    renderScreen(<Data />, { route: '/data?section=manage' });
    await screen.findByRole('tab', { name: 'Manage data' }, { timeout: 8000 });

    // One click on every destructive control. Each must ask before doing
    // anything: two of them used to go straight through, because the server
    // sends a `confirm_phrase` for only two of the seven scopes and the screen
    // treated "no phrase" as "no confirmation needed".
    const dangerous = screen.queryAllByRole('button', { name: /clear|delete|reset|rebuild/i });
    expect(dangerous.length).toBeGreaterThan(0);
    for (const button of dangerous) {
      // eslint-disable-next-line no-await-in-loop
      await user.click(button);
    }
    expect(server.calls.some(
      (c) => c.method === 'POST' && c.path.startsWith('/api/data/clear/'))).toBe(false);
    expect(server.calls.some((c) => c.path === '/api/reset')).toBe(false);
    expect(server.calls.some((c) => c.path === '/api/reanalyze')).toBe(false);
    expect(server.calls.some((c) => c.path.startsWith('/api/data/snapshots/'))).toBe(false);
  });
});

describe('Rules', () => {
  it('lists every institution the app can recognise', async () => {
    renderScreen(<Rules />, { route: '/rules?section=institutions' });
    await waitFor(() => {
      expect(server.lastCall('/api/rules')).toBeTruthy();
    }, { timeout: 8000 });
    const first = fixture('/api/rules').find.institutions[0];
    expect((await screen.findAllByText(
      first.name, {}, { timeout: 8000 })).length).toBeGreaterThan(0);
  });

  it('tests an example against the real rules', async () => {
    const user = userEvent.setup();
    renderScreen(<Rules />, { route: '/rules' });

    const box = await screen.findByPlaceholderText(/UPI\/SWIGGY/i, {}, { timeout: 8000 });
    await user.type(box, 'UPI/SWIGGY/AUG25/123456');
    // "Explain" is also the name of the section tab, which comes first in the
    // document; the one that runs the test is the last.
    const explain = screen.getAllByRole('button', { name: 'Explain' });
    await user.click(explain[explain.length - 1]);

    await waitFor(() => {
      const call = server.lastCall('/api/rules/test', 'POST');
      expect(call).toBeTruthy();
      expect(call.body.description).toContain('SWIGGY');
    }, { timeout: 8000 });
  });

  it('filters the rules on screen without asking the server again', async () => {
    const user = userEvent.setup();
    renderScreen(<Rules />, { route: '/rules?section=institutions' });
    await screen.findByPlaceholderText(/search these rules/i, {}, { timeout: 8000 });

    const before = server.callsTo('/api/rules', 'GET').length;
    await user.type(screen.getByPlaceholderText(/search these rules/i), 'hdfc');
    await new Promise((r) => { setTimeout(r, 400); });
    expect(server.callsTo('/api/rules', 'GET').length).toBe(before);
  });
});

describe('Settings', () => {
  it('stores the model switch on the server, not in this browser', async () => {
    const user = userEvent.setup();
    server = createServer({
      routes: {
        'GET /api/settings': {
          ...fixture('/api/settings'), llm_configured: true, use_llm: false,
        },
      },
    });
    renderScreen(<Settings />, { route: '/settings' });

    const toggle = await screen.findByRole(
      'switch', { name: /use a model/i }, { timeout: 8000 });
    await user.click(toggle);

    await waitFor(() => {
      const call = server.lastCall('/api/settings', 'PUT');
      expect(call).toBeTruthy();
      expect(call.body.use_llm).toBe(true);
    }, { timeout: 8000 });
  });

  it('will not offer a model run while the switch is off', async () => {
    renderScreen(<Settings />, { route: '/settings' });
    const run = await screen.findByRole('button', { name: /categorise \d+ row/i }, {
      timeout: 8000,
    });
    expect(run.disabled).toBe(true);
  });

  it('adds a category, normalising what was typed', async () => {
    const user = userEvent.setup();
    renderScreen(<Settings />, { route: '/settings' });

    const box = await screen.findByPlaceholderText(/e\.g\. pets/i, {}, { timeout: 8000 });
    await user.type(box, 'Side Project');
    await user.click(screen.getByRole('button', { name: /add category/i }));

    await waitFor(() => {
      const call = server.lastCall('/api/categories', 'POST');
      expect(call).toBeTruthy();
      // Lower-cased and underscored, because that is the form every category
      // filter and every stored decision uses.
      expect(call.body.name).toBe('side_project');
    }, { timeout: 8000 });
  });

  it('tells built-in categories from the ones you added', async () => {
    renderScreen(<Settings />, { route: '/settings' });
    // The fixture workspace added exactly one.
    expect(await screen.findByText('Your categories', {}, { timeout: 8000 })).toBeTruthy();
    const custom = fixture('/api/categories/custom');
    expect(custom.length).toBeGreaterThan(0);
    const added = await screen.findByText(
      new RegExp(custom[0], 'i'), {}, { timeout: 8000 });
    expect(added).toBeTruthy();
  });

  it('removes a category only after confirming', async () => {
    const user = userEvent.setup();
    renderScreen(<Settings />, { route: '/settings' });

    const custom = fixture('/api/categories/custom')[0];
    const remove = await screen.findByTitle(
      new RegExp(`remove ${custom}`, 'i'), {}, { timeout: 8000 });
    await user.click(remove);
    expect(server.calls.some((c) => c.method === 'DELETE')).toBe(false);

    await user.click(await screen.findByRole('button', { name: 'Remove' }, { timeout: 8000 }));
    await waitFor(() => {
      expect(server.calls.some(
        (c) => c.method === 'DELETE' && c.path.startsWith('/api/categories/'))).toBe(true);
    }, { timeout: 8000 });
  });

  it('keeps display preferences in this browser and applies them at once', async () => {
    const user = userEvent.setup();
    renderScreen(<Settings />, { route: '/settings' });

    const density = await screen.findByLabelText('Row density', {}, { timeout: 8000 });
    await user.selectOptions(density, 'compact');

    await waitFor(() => {
      const stored = Object.keys(window.localStorage)
        .filter((k) => k.startsWith('prism-prefs'))
        .map((k) => JSON.parse(window.localStorage.getItem(k)));
      expect(stored.some((p) => p.density === 'compact')).toBe(true);
    }, { timeout: 8000 });
    // …and never sent to the server: these are worthless to anyone else.
    expect(server.calls.some(
      (c) => c.method !== 'GET' && /density/.test(JSON.stringify(c.body || {})))).toBe(false);
  });

  it('will not delete the account until the email is typed exactly', async () => {
    const user = userEvent.setup();
    renderScreen(<Settings />, { route: '/settings' });

    const del = await screen.findByRole(
      'button', { name: /delete my account/i }, { timeout: 8000 });
    expect(del.disabled).toBe(true);

    const email = fixture('/api/auth/session').user.email;
    await user.type(screen.getByPlaceholderText(/type your email address/i), email);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /delete my account/i }).disabled).toBe(false);
    }, { timeout: 8000 });
  });
});

describe('Profile', () => {
  it('saves the details that open password-protected statements', async () => {
    const user = userEvent.setup();
    renderScreen(<Profile />, { route: '/profile' });

    const inputs = await screen.findAllByRole('textbox', {}, { timeout: 8000 });
    await user.clear(inputs[0]);
    await user.type(inputs[0], 'Ada Lovelace');
    await user.click(await screen.findByRole('button', { name: /save/i }, { timeout: 8000 }));

    await waitFor(() => {
      const call = server.lastCall('/api/profile', 'PUT');
      expect(call).toBeTruthy();
      expect(JSON.stringify(call.body)).toContain('Ada Lovelace');
    }, { timeout: 8000 });
  });
});
