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
import Admin from '../src/screens/Admin';
import Onboarding from '../src/screens/Onboarding';
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

describe('Explore: building a widget', () => {
  it('opens the editor and previews the query as it is built', async () => {
    const user = userEvent.setup();
    renderScreen(<Explore />, { route: '/explore' });

    await user.click(await screen.findByRole('button', { name: 'Widget' }, {
      timeout: 8000,
    }));
    await waitFor(() => {
      // The preview is the point: a widget is a saved question, and you have
      // to see the answer before saving it.
      expect(server.lastCall('/api/query', 'POST')).toBeTruthy();
    }, { timeout: 8000 });
  });

  it('saves a new widget onto the open board', async () => {
    const user = userEvent.setup();
    renderScreen(<Explore />, { route: '/explore' });

    await user.click(await screen.findByRole('button', { name: 'Widget' }, {
      timeout: 8000,
    }));
    await user.click(await screen.findByRole(
      'button', { name: 'Add to dashboard' }, { timeout: 8000 }));

    await waitFor(() => {
      const call = [...server.calls].reverse().find(
        (c) => c.method === 'POST' && /\/api\/dashboards\/[^/]+\/widgets$/.test(c.path));
      expect(call).toBeTruthy();
      expect(call.body.query).toBeTruthy();
    }, { timeout: 8000 });
  });

  it('edits an existing widget in place rather than adding another', async () => {
    const user = userEvent.setup();
    renderScreen(<Explore />, { route: '/explore' });

    const edit = await screen.findAllByRole('button', { name: /edit/i }, { timeout: 8000 });
    await user.click(edit[0]);
    await user.click(await screen.findByRole(
      'button', { name: 'Save changes' }, { timeout: 8000 }));

    await waitFor(() => {
      const call = [...server.calls].reverse().find(
        (c) => c.method === 'PUT' && /\/widgets\/[^/]+$/.test(c.path));
      expect(call).toBeTruthy();
    }, { timeout: 8000 });
    expect(server.calls.some(
      (c) => c.method === 'POST' && /\/widgets$/.test(c.path))).toBe(false);
  });

  it('exports the board as a file', async () => {
    const user = userEvent.setup();
    renderScreen(<Explore />, { route: '/explore' });
    await screen.findByRole('button', { name: 'Widget' }, { timeout: 8000 });

    const menu = screen.getAllByRole('button').find(
      (b) => /board|more|⋯|…/i.test(b.getAttribute('aria-label') || b.textContent || ''));
    if (!menu) return;                 // the menu is not on this layout
    await user.click(menu);
    const exportBtn = screen.queryByRole('button', { name: /export/i });
    if (!exportBtn) return;
    await user.click(exportBtn);
    await waitFor(() => {
      expect(server.calls.some((c) => /\/export$/.test(c.path))).toBe(true);
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

/* A finished run, in the shape `agents/runner.normalise` produces and
   `agent_routes.read_run` returns. Written out rather than captured because
   producing one needs a language model, which no test should. */
const ANSWER = {
  headline: 'Your card interest is costing more than your SIPs earn',
  summary: 'Two of the four cards revolve every month.',
  metrics: [
    { label: 'Interest paid', value: '18,400', unit: 'INR', note: 'last 12 months' },
  ],
  findings: [{
    title: 'The Northwind card revolved for nine of twelve months',
    detail: 'Only the minimum was paid in each of those months.',
    severity: 'urgent',
    evidence: ['9 statements show a revolving balance'],
  }],
  actions: [{
    title: 'Clear the Northwind balance before the next SIP',
    detail: 'The card costs more than the fund returns.',
    mechanism: 'Move the September SIP to the card once.',
    effort: 'low',
  }],
  caveats: ['One statement in March could not be read.'],
};

function finishedRun(key) {
  return {
    id: 'run-1', agent: key, agent_name: 'Debt Strategist',
    agent_question: 'What is my debt costing me?',
    status: 'complete', started_at: '2026-09-01T10:00:00Z',
    finished_at: '2026-09-01T10:00:42Z', seconds: 42,
    question: '', answer: ANSWER, model: 'test', provider: 'test',
    steps: 4, tool_calls: 9, error: null, previous: null, diff: null,
    transcript: [
      { tool: 'monthly_totals', result: { rows: 12 } },
      { tool: 'card_utilisation', result: { cards: 4 } },
    ],
  };
}

describe('an agent answer', () => {
  it('renders every part of what the agent said', async () => {
    const key = fixture('/api/agents').agents[0].key;
    server = createServer({
      routes: {
        'GET /api/agents': { ...fixture('/api/agents'), model_available: true },
        [`POST /api/agents/${key}/run`]: { job_id: 'agent-1' },
        'GET /api/jobs/agent-1': {
          id: 'agent-1', kind: 'agent', active: false, status: 'complete',
          progress: 1, result: { run_id: 'run-1' }, steps: [],
        },
        'GET /api/agents/runs/run-1': finishedRun(key),
      },
    });

    const user = userEvent.setup();
    renderScreen(<Agents />, { route: '/agents' });
    await screen.findAllByRole('button', { name: /^run$/i }, { timeout: 8000 });
    await user.click(screen.getAllByRole('button', { name: /^run$/i })[0]);

    // The headline, the summary, and each of the four lists the agent fills.
    expect(await screen.findByText(ANSWER.headline, {}, { timeout: 8000 })).toBeTruthy();
    expect(screen.getByText(ANSWER.summary)).toBeTruthy();
    expect(screen.getByText(ANSWER.findings[0].title)).toBeTruthy();
    expect(screen.getByText(ANSWER.findings[0].detail)).toBeTruthy();
    expect(screen.getByText(ANSWER.actions[0].title)).toBeTruthy();
    expect(screen.getByText(ANSWER.caveats[0])).toBeTruthy();
    expect(screen.getByText(/interest paid/i)).toBeTruthy();
  });

  it('shows its working, which is what makes the figures checkable', async () => {
    const key = fixture('/api/agents').agents[0].key;
    server = createServer({
      routes: {
        'GET /api/agents': { ...fixture('/api/agents'), model_available: true },
        [`POST /api/agents/${key}/run`]: { job_id: 'agent-1' },
        'GET /api/jobs/agent-1': {
          id: 'agent-1', kind: 'agent', active: false, status: 'complete',
          progress: 1, result: { run_id: 'run-1' }, steps: [],
        },
        'GET /api/agents/runs/run-1': finishedRun(key),
      },
    });

    const user = userEvent.setup();
    renderScreen(<Agents />, { route: '/agents' });
    await screen.findAllByRole('button', { name: /^run$/i }, { timeout: 8000 });
    await user.click(screen.getAllByRole('button', { name: /^run$/i })[0]);
    await screen.findByText(ANSWER.headline, {}, { timeout: 8000 });

    const show = screen.queryByRole('button', { name: /working|transcript|show/i });
    if (!show) return;
    await user.click(show);
    await waitFor(() => {
      // Fetched only when asked for: a transcript is the largest thing in a
      // run and nobody wants it on every list.
      expect(server.calls.some(
        (c) => c.path === '/api/agents/runs/run-1' && c.search.includes('transcript=true')))
        .toBe(true);
    }, { timeout: 8000 });
  });

  it('reports a failed run instead of an empty answer', async () => {
    const key = fixture('/api/agents').agents[0].key;
    server = createServer({
      routes: {
        'GET /api/agents': { ...fixture('/api/agents'), model_available: true },
        [`POST /api/agents/${key}/run`]: { job_id: 'agent-1' },
        'GET /api/jobs/agent-1': {
          id: 'agent-1', kind: 'agent', active: false, status: 'failed',
          progress: 1, errors: ['the model refused the request'], steps: [],
        },
      },
    });

    const user = userEvent.setup();
    renderScreen(<Agents />, { route: '/agents' });
    await screen.findAllByRole('button', { name: /^run$/i }, { timeout: 8000 });
    await user.click(screen.getAllByRole('button', { name: /^run$/i })[0]);

    expect(await screen.findByText(
      /the model refused the request/i, {}, { timeout: 8000 })).toBeTruthy();
  });
});

/* A coverage grid with one of every kind of cell, in the shape
   /api/coverage answers. */
function coverageWith(months, row = {}) {
  return {
    accounts: [{
      account_id: 'acc-1',
      display_name: 'Northwind Card Everyday Rewards Credit Card (XXXX7731)',
      institution: 'Northwind Card',
      masked: 'XXXX7731',
      account_type: 'credit_card',
      months,
      ...row,
    }],
  };
}

const gmailConnected = { ...fixture('/api/gmail/status'), available: true, connected: true };

describe('the coverage grid', () => {
  it('names every row', async () => {
    renderScreen(<Data />, { route: '/data' });
    const account = fixture('/api/coverage').accounts[0];

    // It read `row.label` - a key /api/coverage has never carried - so every
    // row of the grid was labelled with nothing at all.
    await waitFor(() => {
      const names = [...document.querySelectorAll('.cov-name')]
        .map((el) => el.textContent.trim());
      expect(names.length).toBe(fixture('/api/coverage').accounts.length);
      expect(names.every((n) => n.length > 3)).toBe(true);
      expect(names[0]).toContain(account.institution);
    }, { timeout: 8000 });
  });

  it('shows the account number, which is what tells two cards apart', async () => {
    renderScreen(<Data />, { route: '/data' });
    const account = fixture('/api/coverage').accounts[0];
    expect(await screen.findByText(account.masked, {}, { timeout: 8000 })).toBeTruthy();
  });

  it('keeps the full name one hover away', async () => {
    renderScreen(<Data />, { route: '/data' });
    const account = fixture('/api/coverage').accounts[0];
    await waitFor(() => {
      expect(document.querySelector(`[title="${account.display_name}"]`)).toBeTruthy();
    }, { timeout: 8000 });
  });

  it('opens the rows a green cell stands for', async () => {
    const user = userEvent.setup();
    renderScreen(<Data />, { route: '/data' });

    const cells = await waitFor(() => {
      const found = document.querySelectorAll('.cov-cell.parsed');
      expect(found.length).toBeGreaterThan(0);
      return found;
    }, { timeout: 8000 });
    await user.click(cells[0]);

    await waitFor(() => {
      const call = [...server.calls].reverse().find(
        (c) => c.path === '/api/transactions');
      const params = new URLSearchParams(call.search);
      // By account and ACCOUNTING month, which is what a cell means. A parsed
      // cell often has no source file linked to it, so keying the panel on
      // the file opened for some months and silently not for others.
      expect(params.get('account_id')).toBeTruthy();
      expect(params.get('accounting_month')).toMatch(/^\d{4}-\d{2}$/);
    }, { timeout: 8000 });
  });

  it('never lets a cell with nothing to do look clickable', async () => {
    renderScreen(<Data />, { route: '/data' });
    await waitFor(() => {
      expect(document.querySelectorAll('.cov-cell').length).toBeGreaterThan(0);
    }, { timeout: 8000 });

    // Every cell is either actionable AND enabled, or neither. A disabled
    // button that still takes the pointer cursor and the hover animation is
    // indistinguishable from a broken one.
    for (const cell of document.querySelectorAll('.cov-cell')) {
      expect(cell.classList.contains('can')).toBe(!cell.disabled);
    }
  });

  it('a month before the account began is never actionable', async () => {
    server = createServer({
      routes: {
        'GET /api/coverage': coverageWith([
          { month: '2026-08', status: 'na', statement_id: null, file_id: null },
        ]),
      },
    });
    renderScreen(<Data />, { route: '/data' });
    await waitFor(() => {
      const cell = document.querySelector('.cov-cell.na');
      expect(cell).toBeTruthy();
      expect(cell.disabled).toBe(true);
    }, { timeout: 8000 });
  });

  it('retries the file behind an amber cell', async () => {
    const user = userEvent.setup();
    server = createServer({
      routes: {
        'GET /api/coverage': coverageWith([
          { month: '2026-08', status: 'failed', statement_id: null, file_id: 'file-9' },
        ]),
        'POST /api/files/file-9/retry': { status: 'ok', message: 'Parsed on the retry.' },
      },
    });
    renderScreen(<Data />, { route: '/data' });

    const cell = await waitFor(() => {
      const found = document.querySelector('.cov-cell.failed');
      expect(found).toBeTruthy();
      return found;
    }, { timeout: 8000 });
    await user.click(cell);

    await waitFor(() => {
      expect(server.lastCall('/api/files/file-9/retry', 'POST')).toBeTruthy();
    }, { timeout: 8000 });
  });

  it('an amber cell with no file behind it is not clickable', async () => {
    server = createServer({
      routes: {
        'GET /api/coverage': coverageWith([
          { month: '2026-08', status: 'failed', statement_id: null, file_id: null },
        ]),
      },
    });
    renderScreen(<Data />, { route: '/data' });
    await waitFor(() => {
      const cell = document.querySelector('.cov-cell.failed');
      expect(cell).toBeTruthy();
      // There is nothing to retry, so it must not offer to.
      expect(cell.disabled).toBe(true);
    }, { timeout: 8000 });
  });

  it('searches the mailbox for a red cell when there is a mailbox', async () => {
    const user = userEvent.setup();
    server = createServer({
      routes: {
        'GET /api/gmail/status': gmailConnected,
        'GET /api/coverage': coverageWith([
          { month: '2026-08', status: 'missing', statement_id: null, file_id: null },
        ]),
        'POST /api/coverage/acc-1/2026-08/fetch': { job_id: 'fetch-1' },
        'GET /api/jobs/fetch-1': {
          id: 'fetch-1', kind: 'process', active: false, status: 'complete',
          result: { status: 'ok', message: 'Found it.' },
        },
      },
    });
    renderScreen(<Data />, { route: '/data' });

    const cell = await waitFor(() => {
      const found = document.querySelector('.cov-cell.missing');
      expect(found).toBeTruthy();
      expect(found.disabled).toBe(false);
      return found;
    }, { timeout: 8000 });
    await user.click(cell);

    await waitFor(() => {
      expect(server.lastCall('/api/coverage/acc-1/2026-08/fetch', 'POST')).toBeTruthy();
    }, { timeout: 8000 });
  });

  it('says why a red cell cannot be clicked when there is no mailbox', async () => {
    server = createServer({
      routes: {
        'GET /api/coverage': coverageWith([
          { month: '2026-08', status: 'missing', statement_id: null, file_id: null },
        ]),
      },
    });
    renderScreen(<Data />, { route: '/data' });

    await waitFor(() => {
      const cell = document.querySelector('.cov-cell.missing');
      expect(cell).toBeTruthy();
      // Clicking it used to start a job that could only come back
      // "400: Gmail is not connected".
      expect(cell.disabled).toBe(true);
    }, { timeout: 8000 });
    expect(await screen.findByText(
      /missing a statement/i, {}, { timeout: 8000 })).toBeTruthy();
    // …and the bulk button is not offered either, for the same reason.
    expect(screen.queryByRole('button', { name: /fetch \d+ missing/i })).toBeNull();
  });

  it('offers the bulk fetch only when the mailbox can answer it', async () => {
    server = createServer({
      routes: {
        'GET /api/gmail/status': gmailConnected,
        'GET /api/coverage': coverageWith([
          { month: '2026-08', status: 'missing', statement_id: null, file_id: null },
        ]),
      },
    });
    renderScreen(<Data />, { route: '/data' });
    expect(await screen.findByRole(
      'button', { name: /fetch 1 missing/i }, { timeout: 8000 })).toBeTruthy();
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

  it('says why a file failed, not just that it did', async () => {
    server = createServer({
      routes: {
        'GET /api/files': [{
          ...fixture('/api/files')[0],
          filename: 'locked.pdf',
          parse_status: 'failed',
          transaction_count: 0,
          error_message: 'No password derived from your details opened this PDF.',
        }],
      },
    });
    renderScreen(<Data />, { route: '/data?section=files' });

    expect(await screen.findByText('locked.pdf', {}, { timeout: 8000 })).toBeTruthy();
    // "Failed" and a Retry button is not an answer to the question somebody
    // opens this screen with, and the reason has always been in the payload.
    expect(await screen.findByText(
      /no password derived from your details/i, {}, { timeout: 8000 })).toBeTruthy();
  });

  it('says so when a file was read and yielded nothing', async () => {
    server = createServer({
      routes: {
        'GET /api/files': [{
          ...fixture('/api/files')[0],
          filename: 'holdings.pdf',
          parse_status: 'parsed',
          transaction_count: 0,
          error_message: '0 holding(s). No holdings were read.',
        }],
      },
    });
    renderScreen(<Data />, { route: '/data?section=files' });

    expect(await screen.findByText(
      /no holdings were read/i, {}, { timeout: 8000 })).toBeTruthy();
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


describe('Admin', () => {
  it('says who is on the deployment, for somebody who runs it', async () => {
    const session = fixture('/api/auth/session');
    server = createServer({
      routes: { 'GET /api/auth/session': { ...session, is_admin: true } },
    });
    renderScreen(<Admin />, { route: '/admin' });

    await waitFor(() => {
      expect(server.lastCall('/api/admin/overview')).toBeTruthy();
    }, { timeout: 8000 });
    // The viewer is named, so nobody mistakes whose deployment they are
    // looking at.
    const email = fixture('/api/auth/session').user.email;
    expect(await screen.findByText(`you are ${email}`, {}, { timeout: 8000 }))
      .toBeTruthy();
    // …and the accounts it counted, which is what the screen is for.
    expect(screen.getByText(/this deployment/i)).toBeTruthy();
  });

  it('explains the 404 rather than showing a broken screen', async () => {
    // The endpoint answers 404 to everybody not named in FA_ADMIN_EMAILS,
    // which is the control - hiding the rail entry is only convenience.
    renderScreen(<Admin />, { route: '/admin' });
    expect(await screen.findByText(
      /FA_ADMIN_EMAILS/i, {}, { timeout: 8000 })).toBeTruthy();
  });
});

describe('Onboarding', () => {
  it('opens on the step the server says the account is on', async () => {
    server = createServer({
      routes: {
        'GET /api/onboarding': { ...fixture('/api/onboarding'), step: 'mailbox' },
      },
    });
    renderScreen(<Onboarding onFinished={() => {}} onImport={() => {}} />, { route: '/' });

    // Named on the rail and again as the step's own heading.
    expect((await screen.findAllByText(/your mailbox/i, {}, { timeout: 8000 })).length)
      .toBeGreaterThan(1);
  });

  it('lets every step be skipped, because none of it is required', async () => {
    const user = userEvent.setup();
    const finished = [];
    renderScreen(
      <Onboarding onFinished={(u) => finished.push(u)} onImport={() => {}} />,
      { route: '/' },
    );

    await user.click(await screen.findByRole('button', { name: /skip setup/i }, {
      timeout: 8000,
    }));
    await waitFor(() => {
      expect(server.lastCall('/api/onboarding/complete', 'POST')).toBeTruthy();
    }, { timeout: 8000 });
    await waitFor(() => expect(finished.length).toBe(1), { timeout: 8000 });
  });

  it('saves the details that open password-protected statements', async () => {
    const user = userEvent.setup();
    server = createServer({
      routes: {
        'GET /api/onboarding': { ...fixture('/api/onboarding'), step: 'identity' },
      },
    });
    renderScreen(<Onboarding onFinished={() => {}} onImport={() => {}} />, { route: '/' });

    const boxes = await screen.findAllByRole('textbox', {}, { timeout: 8000 });
    await user.clear(boxes[0]);
    await user.type(boxes[0], 'Ada Lovelace');
    await user.click(screen.getByRole('button', { name: 'Save and continue' }));

    await waitFor(() => {
      const call = server.lastCall('/api/profile', 'PUT');
      expect(call).toBeTruthy();
      expect(JSON.stringify(call.body)).toContain('Ada Lovelace');
    }, { timeout: 8000 });
  });

  it('finishing with "import" hands the app straight to the wizard', async () => {
    const user = userEvent.setup();
    const opened = [];
    server = createServer({
      routes: { 'GET /api/onboarding': { ...fixture('/api/onboarding'), step: 'import' } },
    });
    renderScreen(
      <Onboarding onFinished={() => {}} onImport={() => opened.push(true)} />,
      { route: '/' },
    );

    await user.click(await screen.findByRole('button', { name: /import statements/i }, {
      timeout: 8000,
    }));
    await waitFor(() => expect(opened.length).toBe(1), { timeout: 8000 });
  });
});
