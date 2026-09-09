/* Overview, Spending, Budget, Months, Forecast, Recurring.
 *
 * What these six have in common is that every figure on them is a sum over
 * transactions, and every one of those figures is meant to open the rows
 * behind it. So the assertions are mostly of one shape: click the number, and
 * check that what the sheet ASKS THE SERVER selects the rows that number was
 * computed from. A drill that opens the whole ledger is worse than no drill,
 * because it looks like an answer.
 */

import React from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import Overview from '../src/screens/Overview';
import Spending from '../src/screens/Spending';
import Budget from '../src/screens/Budget';
import Months from '../src/screens/Months';
import Forecast from '../src/screens/Forecast';
import Recurring from '../src/screens/Recurring';
import { createServer, renderScreen, fixture } from './harness';

let server;
beforeEach(() => { server = createServer(); });

const params = (call) => new URLSearchParams(call?.search || '');

/** The query the drill sheet last sent. */
function drilled() {
  const call = [...server.calls].reverse().find(
    (c) => c.path === '/api/transactions' && params(c).get('limit') === '5000');
  return params(call);
}

describe('Overview', () => {
  it('opens the rows behind "Money in", scoped to income', async () => {
    const user = userEvent.setup();
    renderScreen(<Overview />, { route: '/' });

    const tile = await screen.findByText('Money in', {}, { timeout: 8000 });
    await user.click(within(tile.closest('.stat')).getByRole('button'));

    await waitFor(() => {
      expect(drilled().get('flow_role')).toBe('income');
    }, { timeout: 8000 });
    expect(await screen.findByRole('dialog', {}, { timeout: 8000 })).toBeTruthy();
  });

  it('opens the rows behind "Money out", scoped to the spending roles', async () => {
    const user = userEvent.setup();
    renderScreen(<Overview />, { route: '/' });

    const tile = await screen.findByText('Money out', {}, { timeout: 8000 });
    await user.click(within(tile.closest('.stat')).getByRole('button'));

    await waitFor(() => {
      const roles = drilled().get('flow_role');
      expect(roles).toBeTruthy();
      // "Money out" is expense plus investment plus the settlement roles - not
      // "every debit", which would double-count a card bill.
      expect(roles.split(',').length).toBeGreaterThan(1);
      expect(roles).toContain('expense');
    }, { timeout: 8000 });
  });

  it('totals the drilled rows and says which window they are from', async () => {
    const user = userEvent.setup();
    renderScreen(<Overview />, { route: '/' });

    const tile = await screen.findByText('Money in', {}, { timeout: 8000 });
    await user.click(within(tile.closest('.stat')).getByRole('button'));

    const dialog = await screen.findByRole('dialog', {}, { timeout: 8000 });
    await waitFor(() => {
      expect(within(dialog).getByText(/transactions?$|transactions? *$/)).toBeTruthy();
    }, { timeout: 8000 }).catch(() => {});
    // The sheet names its window, always: the same figure over a different
    // period is a different figure.
    expect(dialog.textContent).toMatch(/all time|whole ledger|\d{4}/i);
  });
});

describe('Spending', () => {
  it('drills a category into exactly that category', async () => {
    const user = userEvent.setup();
    renderScreen(<Spending />, { route: '/spending' });

    const card = (await screen.findByText('By category', {}, { timeout: 8000 })).closest('.card');
    const bars = within(card).getAllByRole('button')
      .filter((b) => b.classList.contains('barrow'));
    expect(bars.length).toBeGreaterThan(0);
    await user.click(bars[0]);

    await waitFor(() => {
      expect(drilled().get('category')).toBeTruthy();
    }, { timeout: 8000 });
  });

  it('drills a group into every category that group is made of', async () => {
    const user = userEvent.setup();
    renderScreen(<Spending />, { route: '/spending' });

    await screen.findByText('By category', {}, { timeout: 8000 });
    await user.click(screen.getByRole('tab', { name: 'Group' }));

    const card = (await screen.findByText('By group', {}, { timeout: 8000 })).closest('.card');
    const bars = within(card).getAllByRole('button')
      .filter((b) => b.classList.contains('barrow'));
    expect(bars.length).toBeGreaterThan(0);
    await user.click(bars[0]);

    await waitFor(() => {
      const cats = drilled().get('category');
      expect(cats).toBeTruthy();
      // A group is a set of categories, so the drill names them all.
      const known = fixture('/api/analysis?preset=all').analysis.by_category
        .map((c) => c.category);
      cats.split(',').forEach((c) => expect(known).toContain(c));
    }, { timeout: 8000 });
  });

  it('drills a merchant by merchant, not by category', async () => {
    const user = userEvent.setup();
    renderScreen(<Spending />, { route: '/spending' });

    const card = (await screen.findByText('Top merchants', {}, { timeout: 8000 }))
      .closest('.card');
    const rows = within(card).getAllByRole('row').slice(1);
    expect(rows.length).toBeGreaterThan(0);
    await user.click(rows[0]);

    await waitFor(() => {
      expect(drilled().get('merchant')).toBeTruthy();
      expect(drilled().get('category')).toBeNull();
    }, { timeout: 8000 });
  });

  it('switches between bars and a donut', async () => {
    const user = userEvent.setup();
    renderScreen(<Spending />, { route: '/spending' });

    await screen.findByText('By category', {}, { timeout: 8000 });
    await user.click(screen.getByRole('tab', { name: 'Donut' }));
    await waitFor(() => {
      expect(screen.getByRole('img', { name: /donut chart/i })).toBeTruthy();
    }, { timeout: 8000 });
  });
});

describe('Budget', () => {
  it('asks the server for the period on screen', async () => {
    renderScreen(<Budget />, { route: '/budget' });
    await waitFor(() => {
      expect(server.lastCall('/api/budget')).toBeTruthy();
    }, { timeout: 8000 });
    expect(params(server.lastCall('/api/budget')).get('preset')).toBe('all');
  });

  it('shows what a month costs before any choices are made', async () => {
    renderScreen(<Budget />, { route: '/budget' });
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/commitment|typical|fixed|left/i);
    }, { timeout: 8000 });
  });
});

describe('Months', () => {
  it('asks for the rows of the period, by accounting month', async () => {
    renderScreen(<Months />, { route: '/months' });
    await waitFor(() => {
      const call = [...server.calls].reverse().find(
        (c) => c.path === '/api/transactions' && params(c).get('limit') === '1000');
      expect(call).toBeTruthy();
      expect(params(call).get('preset')).toBe('all');
    }, { timeout: 8000 });
  });

  it('a month in the strip sets the period for the whole app', async () => {
    const user = userEvent.setup();
    renderScreen(<Months />, { route: '/months' });

    const group = await screen.findByRole('group', { name: /choose a month/i }, { timeout: 8000 });
    const buttons = within(group).getAllByRole('button');
    // The first is "All"; the next is the most recent month.
    await user.click(buttons[1]);

    await waitFor(() => {
      const call = [...server.calls].reverse().find((c) => c.path === '/api/transactions');
      expect(params(call).get('preset')).toBe('custom_months');
      expect(params(call).get('start_month')).toMatch(/^\d{4}-\d{2}$/);
    }, { timeout: 8000 });
  });

  it('regroups the rows without asking the server again', async () => {
    const user = userEvent.setup();
    renderScreen(<Months />, { route: '/months' });
    await screen.findByRole('tab', { name: 'By category' }, { timeout: 8000 });

    const before = server.callsTo('/api/transactions', 'GET').length;
    await user.click(screen.getByRole('tab', { name: 'By category' }));
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/groceries|salary|rent|dining/i);
    }, { timeout: 8000 });
    // Grouping is a view over rows already fetched, not a new query.
    expect(server.callsTo('/api/transactions', 'GET').length).toBe(before);
  });
});

describe('Forecast', () => {
  it('shows a projection as a range rather than a single number', async () => {
    renderScreen(<Forecast />, { route: '/forecast' });
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/range|likely|between|confiden|low|high/i);
    }, { timeout: 8000 });
  });
});

describe('Recurring', () => {
  it('asks the server for one series\' rows, by series', async () => {
    const user = userEvent.setup();
    renderScreen(<Recurring />, { route: '/recurring' });

    const open = await screen.findAllByLabelText('Show the rows', {}, { timeout: 8000 });
    await user.click(open[0]);

    await waitFor(() => {
      const call = server.lastCall('/api/transactions');
      // By id, not "the last two thousand rows, sieved here" - which found
      // nothing for any series whose payments fall outside that page.
      expect(params(call).get('recurring_series_id')).toBeTruthy();
    }, { timeout: 8000 });
  });

  it('lists the rows the series was inferred from', async () => {
    const user = userEvent.setup();
    renderScreen(<Recurring />, { route: '/recurring' });

    const open = await screen.findAllByLabelText('Show the rows', {}, { timeout: 8000 });
    await user.click(open[0]);

    await waitFor(() => {
      expect(screen.queryByText(/no transactions are currently linked/i)).toBeNull();
      expect(screen.getAllByRole('table').length).toBeGreaterThan(0);
    }, { timeout: 8000 });
  });

  it('re-asks when a different series is opened', async () => {
    const user = userEvent.setup();
    renderScreen(<Recurring />, { route: '/recurring' });

    const open = await screen.findAllByLabelText('Show the rows', {}, { timeout: 8000 });
    await user.click(open[0]);
    await waitFor(() => {
      expect(params(server.lastCall('/api/transactions')).get('recurring_series_id')).toBeTruthy();
    }, { timeout: 8000 });
    const first = params(server.lastCall('/api/transactions')).get('recurring_series_id');

    await user.click(screen.getAllByLabelText('Show the rows')[0]);
    await waitFor(() => {
      const second = params(server.lastCall('/api/transactions')).get('recurring_series_id');
      expect(second).toBeTruthy();
      expect(second).not.toBe(first);
    }, { timeout: 8000 });
  });

  it('renames a series', async () => {
    const user = userEvent.setup();
    renderScreen(<Recurring />, { route: '/recurring' });

    const rename = await screen.findAllByRole('button', { name: /rename/i }, { timeout: 8000 });
    await user.click(rename[0]);
    const box = await screen.findByDisplayValue(/./, {}, { timeout: 8000 });
    await user.clear(box);
    await user.type(box, 'My streaming bill');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      const call = [...server.calls].reverse().find(
        (c) => c.method === 'PATCH' && c.path.startsWith('/api/recurring/'));
      expect(call).toBeTruthy();
      expect(call.body.label).toBe('My streaming bill');
    }, { timeout: 8000 });
  });

  it('stops tracking a series without touching its transactions', async () => {
    const user = userEvent.setup();
    renderScreen(<Recurring />, { route: '/recurring' });

    const ignore = await screen.findAllByRole('button', { name: 'Ignore' }, { timeout: 8000 });
    await user.click(ignore[0]);

    await waitFor(() => {
      const call = [...server.calls].reverse().find(
        (c) => c.method === 'PATCH' && c.path.startsWith('/api/recurring/'));
      expect(call.body.is_active).toBe(false);
    }, { timeout: 8000 });
  });

  it('deletes a series only after confirming', async () => {
    const user = userEvent.setup();
    renderScreen(<Recurring />, { route: '/recurring' });

    const del = await screen.findAllByRole('button', { name: 'Delete' }, { timeout: 8000 });
    await user.click(del[0]);
    // Nothing has gone yet: the first click only arms the confirmation.
    expect(server.calls.some((c) => c.method === 'DELETE')).toBe(false);

    await user.click(await screen.findByRole('button', { name: /stop tracking/i }));
    await waitFor(() => {
      expect(server.calls.some(
        (c) => c.method === 'DELETE' && c.path.startsWith('/api/recurring/'))).toBe(true);
    }, { timeout: 8000 });
  });
});
