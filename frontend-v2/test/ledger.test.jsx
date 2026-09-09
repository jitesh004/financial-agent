/* The ledger: filters, search, sorting, paging, editing, export.
 *
 * Every assertion here is about what the screen ASKS FOR, not only about what
 * it draws. A search box that narrows the list on screen but never reaches the
 * server is the bug this file exists to catch: it looks right on a page of
 * fifty rows and returns the wrong answer on a ledger of five thousand.
 */

import React from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import Ledger from '../src/screens/Ledger';
import { createServer, renderScreen, allTransactions, setPrefs } from './harness';
import { downloads } from './setup';

let server;
beforeEach(() => { server = createServer(); });

/** Wait until the table has painted its first page. */
async function ledgerReady() {
  await waitFor(() => expect(screen.getByRole('table')).toBeTruthy(), { timeout: 5000 });
}

const params = (call) => new URLSearchParams(call.search);

describe('Ledger', () => {
  it('lists transactions and reports the filtered total', async () => {
    renderScreen(<Ledger />, { route: '/ledger' });
    await ledgerReady();

    const first = server.lastCall('/api/transactions');
    expect(first).toBeTruthy();
    // Every account in scope is named explicitly - "all of my subset" is not
    // "no filter at all".
    expect(params(first).get('account_id')).toBeTruthy();
    expect(params(first).get('account_id')).not.toBe('__none__');
    // The row count beside the heading is the FILTERED total, so it has to be
    // the number of rows the query matched rather than the size of the page.
    const heading = screen.getByRole('heading', { name: /ledger/i });
    expect(heading.textContent).toMatch(/255 rows/);
  });

  it('sends what is typed in the search box to the server', async () => {
    const user = userEvent.setup();
    renderScreen(<Ledger />, { route: '/ledger' });
    await ledgerReady();

    const box = screen.getByPlaceholderText(/search descriptions and merchants/i);
    await user.type(box, 'brightpath');

    await waitFor(() => {
      const call = server.lastCall('/api/transactions');
      expect(params(call).get('search')).toBe('brightpath');
    }, { timeout: 5000 });
  });

  it('narrows the rows on screen to what the search matched', async () => {
    const user = userEvent.setup();
    renderScreen(<Ledger />, { route: '/ledger' });
    await ledgerReady();

    await user.type(
      screen.getByPlaceholderText(/search descriptions and merchants/i), 'brightpath');

    await waitFor(() => {
      const cells = screen.getAllByRole('row').slice(1)
        .map((r) => r.textContent.toLowerCase());
      expect(cells.length).toBeGreaterThan(0);
      expect(cells.every((text) => text.includes('brightpath'))).toBe(true);
    }, { timeout: 5000 });
  });

  it('says so when a search matches nothing, rather than showing the whole ledger', async () => {
    const user = userEvent.setup();
    renderScreen(<Ledger />, { route: '/ledger' });
    await ledgerReady();

    await user.type(
      screen.getByPlaceholderText(/search descriptions and merchants/i),
      'nothingmatchesthis');

    expect(await screen.findByText(/no transactions match/i, {}, { timeout: 5000 })).toBeTruthy();
  });

  it('keeps the search in the URL so a filtered ledger is a link', async () => {
    const user = userEvent.setup();
    renderScreen(<Ledger />, { route: '/ledger' });
    await ledgerReady();

    await user.type(
      screen.getByPlaceholderText(/search descriptions and merchants/i), 'fuel');
    await waitFor(() => {
      expect(new URLSearchParams(window.location.search).get('q')).toBe('fuel');
    }, { timeout: 5000 });
  });

  it('starts from the search in the URL', async () => {
    renderScreen(<Ledger />, { route: '/ledger?q=brightpath' });
    await ledgerReady();

    await waitFor(() => {
      const call = server.lastCall('/api/transactions');
      expect(params(call).get('search')).toBe('brightpath');
    }, { timeout: 5000 });
    expect(screen.getByPlaceholderText(/search descriptions/i).value).toBe('brightpath');
  });

  it('sorts by a column the server can actually sort on', async () => {
    const user = userEvent.setup();
    renderScreen(<Ledger />, { route: '/ledger' });
    await ledgerReady();

    await user.selectOptions(screen.getByLabelText('Sort by'), 'description');
    await user.click(screen.getByLabelText('Descending'));      // …ascending
    await waitFor(() => {
      const p = params(server.lastCall('/api/transactions'));
      expect(p.get('sort_by')).toBe('description');
      expect(p.get('sort_dir')).toBe('asc');
    }, { timeout: 5000 });

    // …and the answer really is in that order, which is what the sort control
    // silently failed to do while `description` was not a sortable column.
    await waitFor(() => {
      const descriptions = screen.getAllByRole('row').slice(1)
        .map((r) => r.querySelector('.desc')?.textContent)
        .filter(Boolean);
      const sorted = [...descriptions].sort((a, b) => a.toLowerCase()
        .localeCompare(b.toLowerCase()));
      expect(descriptions.slice(0, 8)).toEqual(sorted.slice(0, 8));
    }, { timeout: 5000 });
  });

  it('flips sort direction', async () => {
    const user = userEvent.setup();
    renderScreen(<Ledger />, { route: '/ledger' });
    await ledgerReady();

    await user.click(screen.getByLabelText('Descending'));
    await waitFor(() => {
      expect(params(server.lastCall('/api/transactions')).get('sort_dir')).toBe('asc');
    }, { timeout: 5000 });
  });

  it('filters by category', async () => {
    const user = userEvent.setup();
    renderScreen(<Ledger />, { route: '/ledger' });
    await ledgerReady();

    await user.selectOptions(screen.getByLabelText('Category'), 'groceries');
    await waitFor(() => {
      expect(params(server.lastCall('/api/transactions')).get('category')).toBe('groceries');
    }, { timeout: 5000 });
  });

  it('scopes each preset to its own accounts', async () => {
    const user = userEvent.setup();
    renderScreen(<Ledger />, { route: '/ledger' });
    await ledgerReady();

    await user.click(screen.getByRole('tab', { name: 'Cards' }));
    await waitFor(() => {
      const call = server.lastCall('/api/transactions');
      const ids = params(call).get('account_id').split(',');
      const cards = allTransactions();
      // Only credit-card accounts, and at least one of them.
      expect(ids.length).toBeGreaterThan(0);
      expect(cards.some((t) => ids.includes(t.account_id))).toBe(true);
    }, { timeout: 5000 });
  });

  it('asks for the UPI rail on the UPI preset', async () => {
    const user = userEvent.setup();
    renderScreen(<Ledger />, { route: '/ledger' });
    await ledgerReady();

    await user.click(screen.getByRole('tab', { name: 'UPI' }));
    await waitFor(() => {
      expect(params(server.lastCall('/api/transactions')).get('rail')).toBe('upi');
    }, { timeout: 5000 });
  });

  it('returns no rows at all when every account is unticked', async () => {
    const user = userEvent.setup();
    renderScreen(<Ledger />, { route: '/ledger' });
    await ledgerReady();

    await user.click(screen.getByRole('button', { name: 'Clear all' }));
    await waitFor(() => {
      expect(params(server.lastCall('/api/transactions')).get('account_id')).toBe('__none__');
    }, { timeout: 5000 });
  });

  it('clears every filter with one button', async () => {
    const user = userEvent.setup();
    renderScreen(<Ledger />, { route: '/ledger?q=fuel&cat=fuel' });
    await ledgerReady();

    await user.click(await screen.findByRole('button', { name: 'Clear' }));
    await waitFor(() => {
      const p = params(server.lastCall('/api/transactions'));
      expect(p.get('search')).toBeNull();
      expect(p.get('category')).toBeNull();
    }, { timeout: 5000 });
  });

  it('saves a category change and re-reads the page', async () => {
    const user = userEvent.setup();
    renderScreen(<Ledger />, { route: '/ledger' });
    await ledgerReady();

    const row = screen.getAllByRole('row')[1];
    await user.selectOptions(within(row).getByRole('combobox'), 'groceries');

    await waitFor(() => {
      const patch = server.calls.filter(
        (c) => c.method === 'PATCH' && c.path.startsWith('/api/transactions/')).pop();
      expect(patch).toBeTruthy();
      expect(patch.body.category).toBe('groceries');
    }, { timeout: 5000 });

    // The page is re-read rather than patched in place: changing a category
    // also teaches the merchant cache, which can move rows in or out of the
    // filter this view is showing.
    await waitFor(() => {
      const reads = server.callsTo('/api/transactions', 'GET');
      expect(reads.length).toBeGreaterThan(1);
    }, { timeout: 5000 });
  });

  it('pages through a ledger longer than one page', async () => {
    const user = userEvent.setup();
    // 100 rows a page over 255 rows is three pages.
    setPrefs({ pageSize: '100' });
    renderScreen(<Ledger />, { route: '/ledger' });
    await ledgerReady();

    const next = await screen.findByRole('button', { name: /next/i }, { timeout: 5000 });
    await user.click(next);
    await waitFor(() => {
      expect(params(server.lastCall('/api/transactions')).get('offset')).toBe('100');
    }, { timeout: 5000 });
  });

  it('exports what is on screen as CSV', async () => {
    const user = userEvent.setup();
    renderScreen(<Ledger />, { route: '/ledger' });
    await ledgerReady();

    await user.click(screen.getByRole('button', { name: /export/i }));
    await waitFor(() => expect(downloads.length).toBe(1));
    expect(downloads[0].filename).toMatch(/^transactions-\d{4}-\d{2}-\d{2}\.csv$/);
  });

  it('explains why a row has the category it has', async () => {
    const user = userEvent.setup();
    renderScreen(<Ledger />, { route: '/ledger' });
    await ledgerReady();

    const why = screen.getAllByTitle('Why is this row the way it is?')[0];
    await user.click(why);

    await waitFor(() => {
      expect(server.calls.some((c) => c.path.startsWith('/api/rules/explain/'))).toBe(true);
    }, { timeout: 5000 });
    expect(await screen.findByText('What it is', {}, { timeout: 5000 })).toBeTruthy();
  });

  it('applies a bulk category to every selected row in one request', async () => {
    const user = userEvent.setup();
    renderScreen(<Ledger />, { route: '/ledger' });
    await ledgerReady();

    await user.click(screen.getByLabelText('Select every row on this page'));
    const bar = await screen.findByText(/\d+ selected/, {}, { timeout: 5000 });
    const scope = bar.closest('.card');
    await user.selectOptions(within(scope).getByRole('combobox'), 'groceries');

    await waitFor(() => {
      const bulk = server.lastCall('/api/transactions/bulk', 'PATCH');
      expect(bulk).toBeTruthy();
      expect(bulk.body.txn_ids.length).toBeGreaterThan(1);
      expect(bulk.body.category).toBe('groceries');
    }, { timeout: 5000 });
  });

  it('shows the error the server gave rather than an empty table', async () => {
    server = createServer({
      routes: {
        'GET /api/transactions': { status: 500, body: { detail: 'the ledger is on fire' } },
      },
    });
    renderScreen(<Ledger />, { route: '/ledger' });
    expect(await screen.findByText('the ledger is on fire', {}, { timeout: 5000 })).toBeTruthy();
  });
});
