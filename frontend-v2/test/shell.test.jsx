/* The shell: the door, the rail, the period bar, the palette and the router.
 *
 * Everything here is true on every screen, so a fault in any of it is a fault
 * in all twenty - which is exactly the kind of thing a per-screen test misses.
 */

import React from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import Gate from '../src/app/Gate';
import PeriodBar from '../src/app/PeriodBar';
import CommandPalette from '../src/app/CommandPalette';
import Rail from '../src/app/Rail';
import Ledger from '../src/screens/Ledger';
import { ROUTES } from '../src/app/routes';
import { createServer, renderScreen, renderApp, fixture } from './harness';

let server;
beforeEach(() => { server = createServer(); });

describe('the door', () => {
  it('shows the sign-in screen when there is no session', async () => {
    server = createServer({
      routes: { 'GET /api/auth/session': { status: 401, body: { detail: 'no session' } } },
    });
    renderApp(<Gate />);
    expect(await screen.findByRole(
      'button', { name: /continue with google/i }, { timeout: 8000 })).toBeTruthy();
  });

  it('opens the setup wizard for somebody who has not finished it', async () => {
    const session = fixture('/api/auth/session');
    server = createServer({
      routes: {
        'GET /api/auth/session': {
          ...session, user: { ...session.user, onboarded: false, onboarding_step: 'welcome' },
        },
      },
    });
    renderApp(<Gate />);
    await waitFor(() => {
      expect(server.lastCall('/api/onboarding')).toBeTruthy();
    }, { timeout: 8000 });
  });

  it('opens the app itself for a signed-in, set-up account', async () => {
    renderApp(<Gate />);
    // The rail is the shell's own furniture; a screen alone would not have it.
    expect(await screen.findByRole('navigation', {}, { timeout: 8000 })).toBeTruthy();
  });

  it('does not send a session cookie question anywhere but the auth endpoints',
    async () => {
      renderApp(<Gate />);
      await screen.findByRole('navigation', {}, { timeout: 8000 });
      // The session is read once on load, not per screen.
      expect(server.callsTo('/api/auth/session', 'GET').length).toBe(1);
    });
});

describe('the rail', () => {
  it('offers every screen that works without a ledger, and no more', async () => {
    renderScreen(
      <Rail collapsed={false} onToggle={() => {}} open onClose={() => {}}
        hasLedger={false} reviewCount={0} />,
      { route: '/' },
    );
    const nav = await screen.findByRole('navigation', {}, { timeout: 8000 });

    for (const route of ROUTES) {
      if (route.hidden || route.adminOnly) continue;
      const link = within(nav).queryByText(route.label);
      if (route.needsLedger) {
        expect(link, `${route.label} needs a ledger and should not be offered`).toBeNull();
      } else {
        expect(link, `${route.label} works without a ledger and should be offered`)
          .toBeTruthy();
      }
    }
  });

  it('offers every screen once a ledger exists', async () => {
    renderScreen(
      <Rail collapsed={false} onToggle={() => {}} open onClose={() => {}}
        hasLedger reviewCount={3} />,
      { route: '/' },
    );
    const nav = await screen.findByRole('navigation', {}, { timeout: 8000 });
    for (const route of ROUTES) {
      if (route.hidden || route.adminOnly) continue;
      expect(within(nav).queryByText(route.label), route.label).toBeTruthy();
    }
  });

  it('renders every destination as a real anchor, so it can be opened in a tab',
    async () => {
      renderScreen(
        <Rail collapsed={false} onToggle={() => {}} open onClose={() => {}}
          hasLedger reviewCount={0} />,
        { route: '/' },
      );
      const nav = await screen.findByRole('navigation', {}, { timeout: 8000 });
      const links = within(nav).getAllByRole('link');
      expect(links.length).toBeGreaterThan(5);
      for (const link of links) {
        expect(link.getAttribute('href')).toMatch(/^\//);
      }
    });

  it('shows how many rows are waiting in Review', async () => {
    renderScreen(
      <Rail collapsed={false} onToggle={() => {}} open onClose={() => {}}
        hasLedger reviewCount={7} />,
      { route: '/' },
    );
    const nav = await screen.findByRole('navigation', {}, { timeout: 8000 });
    expect(within(nav).getByText('7')).toBeTruthy();
  });
});

describe('the period bar', () => {
  it('offers the presets the server resolved, labelled with what they resolve to',
    async () => {
      renderScreen(<PeriodBar />, { route: '/' });
      const bar = await screen.findByRole('tablist', { name: 'Period' }, { timeout: 8000 });
      const quick = fixture('/api/periods').presets.filter(
        (p) => ['all', 'this_month', 'last_month', 'last_3m', 'last_6m', 'last_12m']
          .includes(p.value));
      for (const preset of quick) {
        expect(within(bar).getByRole('tab', { name: preset.short || preset.label })).toBeTruthy();
      }
    });

  it('sets the period for every screen at once', async () => {
    const user = userEvent.setup();
    renderScreen(<><PeriodBar /><Ledger /></>, { route: '/ledger' });

    await waitFor(() => expect(screen.getByRole('table')).toBeTruthy(), { timeout: 8000 });
    await user.click(screen.getByRole('tab', { name: 'Last' }));

    await waitFor(() => {
      const call = [...server.calls].reverse().find((c) => c.path === '/api/transactions');
      expect(new URLSearchParams(call.search).get('preset')).toBe('last_month');
    }, { timeout: 8000 });
  });

  it('draws a window by hand, in whole months', async () => {
    const user = userEvent.setup();
    renderScreen(<><PeriodBar /><Ledger /></>, { route: '/ledger' });
    await waitFor(() => expect(screen.getByRole('table')).toBeTruthy(), { timeout: 8000 });

    await user.selectOptions(screen.getByLabelText('Other periods'), 'custom_months');
    await waitFor(() => {
      const call = [...server.calls].reverse().find((c) => c.path === '/api/transactions');
      const p = new URLSearchParams(call.search);
      expect(p.get('preset')).toBe('custom_months');
      expect(p.get('start_month')).toMatch(/^\d{4}-\d{2}$/);
    }, { timeout: 8000 });
  });

  it('goes back to the whole ledger with one click', async () => {
    const user = userEvent.setup();
    renderScreen(<><PeriodBar /><Ledger /></>, { route: '/ledger' });
    await waitFor(() => expect(screen.getByRole('table')).toBeTruthy(), { timeout: 8000 });

    const whole = fixture('/api/transactions?limit=1000&sort_by=date&sort_dir=desc').total;
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /ledger/i }).textContent)
        .toContain(`${whole} rows`);
    }, { timeout: 8000 });

    await user.click(screen.getByRole('tab', { name: 'Last' }));
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /ledger/i }).textContent)
        .not.toContain(`${whole} rows`);
    }, { timeout: 8000 });

    await user.click(await screen.findByRole('button', { name: 'Clear' }, { timeout: 8000 }));
    // Answered from the cache rather than re-fetched - which is the point of
    // stale-while-revalidate - so what proves it worked is the figure on
    // screen, not another request.
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /ledger/i }).textContent)
        .toContain(`${whole} rows`);
    }, { timeout: 8000 });
  });

  it('remembers the period across a reload', async () => {
    const user = userEvent.setup();
    const { unmount } = renderScreen(<PeriodBar />, { route: '/' });
    await screen.findByRole('tablist', { name: 'Period' }, { timeout: 8000 });
    await user.click(screen.getByRole('tab', { name: 'Last' }));
    unmount();

    renderScreen(<PeriodBar />, { route: '/' });
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: 'Last' }).getAttribute('aria-selected'))
        .toBe('true');
    }, { timeout: 8000 });
  });
});

describe('the command palette', () => {
  it('finds a screen by a subsequence of its name', async () => {
    const user = userEvent.setup();
    renderScreen(
      <CommandPalette open onClose={() => {}} onImport={() => {}} hasLedger />,
      { route: '/' },
    );

    const box = await screen.findByRole('textbox', {}, { timeout: 8000 });
    await user.type(box, 'cr');
    expect(await screen.findByText('Credit report', {}, { timeout: 8000 })).toBeTruthy();
  });

  it('navigates to what is chosen', async () => {
    const user = userEvent.setup();
    renderScreen(
      <CommandPalette open onClose={() => {}} onImport={() => {}} hasLedger />,
      { route: '/' },
    );

    const box = await screen.findByRole('textbox', {}, { timeout: 8000 });
    await user.type(box, 'ledger{Enter}');
    await waitFor(() => {
      expect(window.location.pathname).toBe('/ledger');
    }, { timeout: 8000 });
  });

  it('offers nothing that needs a ledger when there is none', async () => {
    renderScreen(
      <CommandPalette open onClose={() => {}} onImport={() => {}} hasLedger={false} />,
      { route: '/' },
    );
    await screen.findByRole('textbox', {}, { timeout: 8000 });
    expect(screen.queryByText('Ledger')).toBeNull();
    // …and still offers the ones that work from empty.
    expect(screen.getByText('Rules')).toBeTruthy();
  });

  it('sets the period from the palette', async () => {
    const user = userEvent.setup();
    renderScreen(
      <><CommandPalette open onClose={() => {}} onImport={() => {}} hasLedger />
        <Ledger /></>,
      { route: '/ledger' },
    );

    const box = await screen.findByRole('textbox', {}, { timeout: 8000 });
    await user.type(box, 'last month{Enter}');
    await waitFor(() => {
      const call = [...server.calls].reverse().find((c) => c.path === '/api/transactions');
      expect(new URLSearchParams(call.search).get('preset')).toBe('last_month');
    }, { timeout: 8000 });
  });
});

describe('routing', () => {
  it('every route in the table has a screen behind it', () => {
    for (const route of ROUTES) {
      expect(typeof route.load, route.path).toBe('function');
      expect(route.title, route.path).toBeTruthy();
      expect(route.label, route.path).toBeTruthy();
    }
  });

  it('every route path is unique', () => {
    const paths = ROUTES.map((r) => r.path);
    expect(new Set(paths).size).toBe(paths.length);
  });

  it('every route key is unique', () => {
    const keys = ROUTES.map((r) => r.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it('every grouped route names a group that exists', async () => {
    const { GROUPS } = await import('../src/app/routes');
    const known = new Set(GROUPS.map((g) => g.key));
    for (const route of ROUTES) {
      if (route.hidden) continue;
      expect(known.has(route.group), `${route.path} is in group "${route.group}"`).toBe(true);
    }
  });
});
