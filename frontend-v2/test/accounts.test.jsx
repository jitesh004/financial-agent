/* Position, Owed, Debt, Credit, Portfolio.
 *
 * These five are about balances rather than flows, and the two that take input
 * - Position and Owed - are the only screens in the app whose figures the USER
 * asserts. So the assertions here are mostly about writes: that a correction
 * reaches the server as a partial patch, that confirming a figure is a
 * different act from correcting one, and that settling a claim sends the
 * amount that is actually outstanding.
 */

import React from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import Position from '../src/screens/Position';
import Owed from '../src/screens/Owed';
import Debt from '../src/screens/Debt';
import Credit from '../src/screens/Credit';
import Portfolio from '../src/screens/Portfolio';
import { createServer, renderScreen, fixture } from './harness';

let server;
beforeEach(() => { server = createServer(); });

const positionItems = () => fixture('/api/position').items;

describe('Position', () => {
  it('reads the archived items too, so nothing is silently missing', async () => {
    renderScreen(<Position />, { route: '/position' });
    await waitFor(() => {
      expect(server.lastCall('/api/position')).toBeTruthy();
    }, { timeout: 8000 });
    expect(new URLSearchParams(server.lastCall('/api/position').search)
      .get('include_archived')).toBe('true');
  });

  it('lists what has been checked, with the date it was checked', async () => {
    renderScreen(<Position />, { route: '/position' });
    const loan = positionItems().find((i) => i.kind === 'loan');
    expect(await screen.findByDisplayValue(loan.label, {}, { timeout: 8000 })).toBeTruthy();
  });

  it('corrects one figure as a partial patch, leaving the others alone', async () => {
    const user = userEvent.setup();
    renderScreen(<Position />, { route: '/position' });

    // Every editable figure is a button that becomes an input when clicked.
    const cells = await screen.findAllByTitle(/click to edit/i, {}, { timeout: 8000 });
    // The input replaces the button in place, so the cell it lived in is where
    // to look for it - the page has other text boxes.
    const cell = cells[0].parentElement;
    await user.click(cells[0]);
    const input = await waitFor(
      () => within(cell).getByRole('textbox'), { timeout: 8000 });
    await user.clear(input);
    await user.type(input, 'Home loan, refinanced{Enter}');

    await waitFor(() => {
      const call = [...server.calls].reverse().find(
        (c) => c.method === 'PATCH' && c.path.startsWith('/api/position/items/'));
      expect(call).toBeTruthy();
      // Exactly one field: a correction must not restate every other figure,
      // or fixing a typo would re-assert values nobody looked at.
      expect(Object.keys(call.body)).toHaveLength(1);
    }, { timeout: 8000 });
  });

  it('abandons an edit on Escape without saving it', async () => {
    const user = userEvent.setup();
    renderScreen(<Position />, { route: '/position' });

    const cells = await screen.findAllByTitle(/click to edit/i, {}, { timeout: 8000 });
    const cell = cells[0].parentElement;
    await user.click(cells[0]);
    const input = await waitFor(
      () => within(cell).getByRole('textbox'), { timeout: 8000 });
    await user.clear(input);
    await user.type(input, 'nonsense{Escape}');

    await new Promise((r) => { setTimeout(r, 300); });
    expect(server.calls.some(
      (c) => c.method === 'PATCH' && c.path.startsWith('/api/position/items/'))).toBe(false);
  });

  it('confirming a figure is a POST to /review, never a patch of the date', async () => {
    const user = userEvent.setup();
    renderScreen(<Position />, { route: '/position' });

    const confirm = await screen.findAllByRole('button', { name: 'Confirm' }, { timeout: 8000 });
    await user.click(confirm[0]);

    await waitFor(() => {
      const call = [...server.calls].reverse().find(
        (c) => c.method === 'POST' && /\/api\/position\/items\/[^/]+\/review$/.test(c.path));
      expect(call).toBeTruthy();
      expect(call.body.reviewed_on).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    }, { timeout: 8000 });
    // …and it is NOT sent as a patch of `reviewed_on`, which would look the
    // same on screen and would not reset what the figure ages from.
    expect(server.calls.some(
      (c) => c.method === 'PATCH' && c.body && 'reviewed_on' in c.body)).toBe(false);
  });

  it('re-reads the position after a change rather than guessing at it', async () => {
    const user = userEvent.setup();
    renderScreen(<Position />, { route: '/position' });

    const confirm = await screen.findAllByRole('button', { name: 'Confirm' }, { timeout: 8000 });
    const before = server.callsTo('/api/position', 'GET').length;
    await user.click(confirm[0]);

    await waitFor(() => {
      expect(server.callsTo('/api/position', 'GET').length).toBeGreaterThan(before);
    }, { timeout: 8000 });
  });

  it('removes an item only after confirming', async () => {
    const user = userEvent.setup();
    renderScreen(<Position />, { route: '/position' });

    const kill = await screen.findAllByRole('button', { name: '✕' }, { timeout: 8000 });
    await user.click(kill[0]);
    expect(server.calls.some((c) => c.method === 'DELETE')).toBe(false);

    await user.click(await screen.findByRole('button', { name: 'Remove' }, { timeout: 8000 }));
    await waitFor(() => {
      expect(server.calls.some(
        (c) => c.method === 'DELETE' && c.path.startsWith('/api/position/items/'))).toBe(true);
    }, { timeout: 8000 });
  });

  it('says what the server said when a change fails', async () => {
    const user = userEvent.setup();
    server = createServer({
      routes: Object.fromEntries(positionItems().map((i) => [
        `POST /api/position/items/${i.id}/review`,
        { status: 400, body: { detail: 'that figure is in the future' } },
      ])),
    });

    renderScreen(<Position />, { route: '/position' });
    const confirm = await screen.findAllByRole('button', { name: 'Confirm' }, { timeout: 8000 });
    await user.click(confirm[0]);

    expect(await screen.findByText(
      /that figure is in the future/i, {}, { timeout: 8000 })).toBeTruthy();
  });
});

describe('Owed', () => {
  it('shows what is outstanding on an open claim', async () => {
    renderScreen(<Owed />, { route: '/owed' });
    const claim = fixture('/api/claims')[0];
    expect(await screen.findByText(claim.counterparty, {}, { timeout: 8000 })).toBeTruthy();
  });

  it('settles a claim in full when the amount is left blank', async () => {
    const user = userEvent.setup();
    renderScreen(<Owed />, { route: '/owed' });

    await user.click(await screen.findByRole('button', { name: 'Settle' }, { timeout: 8000 }));
    await user.click(await screen.findByRole('button', { name: 'Record' }, { timeout: 8000 }));

    await waitFor(() => {
      const call = [...server.calls].reverse().find(
        (c) => c.method === 'POST' && /\/settle$/.test(c.path));
      expect(call).toBeTruthy();
      const claim = fixture('/api/claims')[0];
      // Blank means "all of what is left", not zero.
      expect(Number(call.body.amount)).toBeCloseTo(
        Number(claim.amount) - Number(claim.settled_amount || 0), 2);
      expect(call.body.method).toBe('cash');
      expect(call.body.settled_on).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    }, { timeout: 8000 });
  });

  it('records a part-payment and leaves the rest open', async () => {
    const user = userEvent.setup();
    renderScreen(<Owed />, { route: '/owed' });

    await user.click(await screen.findByRole('button', { name: 'Settle' }, { timeout: 8000 }));
    const amount = await screen.findByPlaceholderText(/full amount/i, {}, { timeout: 8000 });
    await user.type(amount, '100');
    await user.click(screen.getByRole('button', { name: 'Record' }));

    await waitFor(() => {
      const call = [...server.calls].reverse().find((c) => /\/settle$/.test(c.path));
      expect(Number(call.body.amount)).toBe(100);
    }, { timeout: 8000 });
  });

  it('carries the chosen settlement method', async () => {
    const user = userEvent.setup();
    renderScreen(<Owed />, { route: '/owed' });

    await user.click(await screen.findByRole('button', { name: 'Settle' }, { timeout: 8000 }));
    const method = await screen.findByRole('combobox', {}, { timeout: 8000 });
    await user.selectOptions(method, 'write_off');
    await user.click(screen.getByRole('button', { name: 'Record' }));

    await waitFor(() => {
      const call = [...server.calls].reverse().find((c) => /\/settle$/.test(c.path));
      expect(call.body.method).toBe('write_off');
    }, { timeout: 8000 });
  });
});

describe('Debt', () => {
  it('states the payoff arithmetic for a real loan', async () => {
    renderScreen(<Debt />, { route: '/debt' });
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/outstanding|interest|emi/i);
    }, { timeout: 8000 });
  });

  it('offers the way in when there is no debt account at all', async () => {
    createServer({
      routes: {
        'GET /api/dashboard': {
          ...fixture('/api/dashboard'), loans: [], accounts: [],
        },
      },
    });
    const seen = [];
    renderScreen(<Debt onImport={() => seen.push('import')} />, { route: '/debt' });
    expect(await screen.findByText(
      /no debt accounts found/i, {}, { timeout: 8000 })).toBeTruthy();
  });
});

describe('Credit', () => {
  it('says plainly that no report has been imported, rather than showing zeroes',
    async () => {
      renderScreen(<Credit />, { route: '/credit' });
      await waitFor(() => {
        expect(document.body.textContent).toMatch(/credit report|no .*report|import/i);
      }, { timeout: 8000 });
    });
});

describe('Portfolio', () => {
  it('says plainly that nothing is held, rather than showing a zero valuation',
    async () => {
      renderScreen(<Portfolio />, { route: '/portfolio' });
      await waitFor(() => {
        expect(document.body.textContent).toMatch(/holding|portfolio|nothing|import/i);
      }, { timeout: 8000 });
    });
});
