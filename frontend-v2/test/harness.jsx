/* A server the screens cannot tell from the real one.
 *
 * The screens under test are not given mocked modules. They call `core/api`,
 * which calls `fetch`, which lands here - so what a test exercises is the code
 * path the browser runs, including the query string the screen builds, the
 * request the API layer makes of it, and the shape it reads back.
 *
 * The answers come from `fixtures/api.json`, captured from the real FastAPI
 * app over the demo ledger (backend/tools/dump_v2_fixtures.py). Nothing here
 * is hand-written data.
 *
 * `/api/transactions` is the exception that has to be re-implemented rather
 * than replayed, because it is a query rather than a document: a test for the
 * ledger's search box is worthless if the fake answers the same 255 rows
 * whatever it is asked. The filters below mirror
 * `repository._transaction_filters`, and `backend/tests/test_v2_contract.py`
 * asserts the real server agrees with this one on the same parameters - so a
 * fake that drifts is a failing test, not a false pass.
 */

import React from 'react';
import { render } from '@testing-library/react';
import { vi } from 'vitest';
import fixtures from './fixtures/api.json';

import { AuthProvider, useAuth } from '../src/core/auth';
import { RouterProvider } from '../src/core/router';
import { ToastProvider } from '../src/core/toast';
import { PeriodProvider } from '../src/core/period';
import { DrillProvider } from '../src/core/drill';
import { clearCache } from '../src/core/store';

const TXN_FIXTURE = '/api/transactions?limit=1000&sort_by=date&sort_dir=desc';

/** A deep copy, so one test's mutations never reach the next one's fixtures. */
const clone = (value) => JSON.parse(JSON.stringify(value));

export const fixture = (path) => clone(fixtures[path]);

/** Every transaction the demo ledger holds, as the API serialises them. */
export const allTransactions = () => clone(fixtures[TXN_FIXTURE].transactions);

/* ── the query /api/transactions answers ─────────────────────────────────── */

const asList = (value) => (value ? String(value).split(',').filter(Boolean) : null);

/* The month a row is COUNTED in, which is not always the month of its date -
   the server's `effective_month` expression, in one line. */
const monthOf = (t) => t.accounting_month || String(t.date).slice(0, 7);

/* Mirrors repository.MERCHANT_KEY. */
const merchantKey = (t) => (t.merchant || '').trim()
  || (t.description || '').trim();

const SORT_KEYS = {
  date: (t) => t.date,
  amount: (t) => Number(t.amount) || 0,
  balance: (t) => Number(t.balance_after) || 0,
  description: (t) => String(t.description || '').toLowerCase(),
  category: (t) => String(t.category || '').toLowerCase(),
  merchant: (t) => merchantKey(t).toLowerCase(),
};

function resolvePeriod(params) {
  const preset = params.get('preset');
  if (!preset || preset === 'all') return { start_month: null, end_month: null };
  if (preset === 'custom_months') {
    return {
      start_month: params.get('start_month') || null,
      end_month: params.get('end_month') || null,
    };
  }
  if (preset === 'custom') return { start: params.get('start'), end: params.get('end') };
  const resolved = fixtures['/api/periods'].presets.find((p) => p.value === preset);
  return {
    start_month: resolved?.start_month || null,
    end_month: resolved?.end_month || null,
  };
}

export function queryTransactions(rows, search) {
  const params = new URLSearchParams(search);
  const accounts = asList(params.get('account_id'));
  const categories = asList(params.get('category'));
  const roles = asList(params.get('flow_role'));
  const rail = params.get('rail');
  const needle = (params.get('search') || '').trim().toLowerCase();
  const merchant = params.get('merchant');
  const seriesId = params.get('recurring_series_id');
  const needsReview = params.get('needs_review');
  const accountingMonth = params.get('accounting_month');
  const period = resolvePeriod(params);

  let out = rows.filter((t) => {
    if (accounts && !accounts.includes(t.account_id)) return false;
    if (categories && !categories.includes(t.category)) return false;
    if (roles && !roles.includes(t.flow_role)) return false;
    // The server keeps a lender's own ledger out of the list unless a caller
    // asks for that role by name.
    if (!roles && t.flow_role === 'lender_ledger') return false;
    if (rail === 'upi' && !/^upi/i.test(t.description || '')) return false;
    if (rail === 'non_upi' && /^upi/i.test(t.description || '')) return false;
    if (needle) {
      const hay = `${t.description || ''} ${t.merchant || ''}`.toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    if (merchant && !merchantKey(t).toLowerCase().startsWith(merchant.toLowerCase())) return false;
    if (seriesId && t.recurring_series_id !== seriesId) return false;
    if (needsReview != null && needsReview !== '') {
      const want = needsReview === 'true' || needsReview === '1';
      if (Boolean(t.needs_review) !== want) return false;
    }
    if (accountingMonth && monthOf(t) !== accountingMonth) return false;
    if (period.start_month && monthOf(t) < period.start_month) return false;
    if (period.end_month && monthOf(t) > period.end_month) return false;
    if (period.start && t.date < period.start) return false;
    if (period.end && t.date > period.end) return false;
    return true;
  });

  const key = SORT_KEYS[params.get('sort_by') || 'date'] || SORT_KEYS.date;
  const dir = String(params.get('sort_dir') || 'asc').startsWith('desc') ? -1 : 1;
  out = [...out].sort((a, b) => {
    const x = key(a);
    const y = key(b);
    if (x < y) return -1 * dir;
    if (x > y) return 1 * dir;
    return a.id < b.id ? -1 : 1;      // the server's stable secondary key
  });

  const total = out.length;
  const requested = Number(params.get('limit') ?? 200);
  // The server caps a page at 1000 however many were asked for.
  const limit = Math.min(Number.isFinite(requested) ? requested : 200, 1000);
  const offset = Number(params.get('offset') ?? 0) || 0;

  return {
    transactions: out.slice(offset, offset + limit),
    limit: requested,
    offset,
    range: fixtures['/api/analysis?preset=all'].range,
    total,
  };
}

/* ── the server ──────────────────────────────────────────────────────────── */

/**
 * Install a fake API for the duration of a test.
 *
 * `routes` overrides or adds handlers, keyed `"GET /api/thing"`. A handler is
 * either a value (answered as 200 JSON) or a function
 * `({ path, search, body, params }) => value | { status, body }`.
 */
export function createServer({ routes = {}, transactions } = {}) {
  const calls = [];
  const state = {
    transactions: transactions ? clone(transactions) : allTransactions(),
    categories: clone(fixtures['/api/categories']),
    customCategories: clone(fixtures['/api/categories/custom']),
    dashboards: clone(fixtures['/api/dashboards']),
    recurring: clone(fixtures['/api/recurring']),
    position: clone(fixtures['/api/position']),
    claims: clone(fixtures['/api/claims']),
    profile: clone(fixtures['/api/profile']),
    settings: clone(fixtures['/api/settings']),
    ignoredSenders: clone(fixtures['/api/gmail/ignored']),
    boardDetail: fixtures['dashboard:detail']
      ? { [fixtures['dashboard:detail'].id]: clone(fixtures['dashboard:detail']) } : {},
    jobs: {},
  };

  const json = (body, status = 200) => new Response(
    status === 204 ? null : JSON.stringify(body),
    { status, headers: { 'Content-Type': 'application/json' } },
  );

  function handle(method, path, search, body) {
    /* An exact key first, then a `…/*` prefix - so a route whose URL carries
       an id ("GET /api/rules/explain/*") can be overridden without the test
       having to know which id the screen will ask for. */
    const key = `${method} ${path}`;
    const matched = Object.prototype.hasOwnProperty.call(routes, key) ? key
      : Object.keys(routes).find(
        (k) => k.endsWith('*') && key.startsWith(k.slice(0, -1)));
    if (matched) {
      const route = routes[matched];
      const value = typeof route === 'function'
        ? route({ path, search, body, params: new URLSearchParams(search) })
        : route;
      if (value && typeof value === 'object' && 'status' in value && 'body' in value) {
        return json(value.body, value.status);
      }
      return json(value ?? null);
    }

    /* ---- the ledger query ---- */
    if (method === 'GET' && path === '/api/transactions') {
      return json(queryTransactions(state.transactions, search));
    }

    /* ---- writes that a screen reads back ---- */
    if (method === 'PATCH' && path.startsWith('/api/transactions/')) {
      const id = path.split('/').pop();
      if (id === 'bulk') {
        const { txn_ids: ids = [], ...fields } = body || {};
        state.transactions = state.transactions.map(
          (t) => (ids.includes(t.id) ? { ...t, ...fields, needs_review: false } : t));
        return json({ updated: ids.length });
      }
      const updated = state.transactions.map(
        (t) => (t.id === id ? { ...t, ...(body || {}), needs_review: false } : t));
      state.transactions = updated;
      // The real endpoint answers with the row as it now stands, and the drill
      // sheet writes that straight into its cache.
      return json({ status: 'ok', transaction: updated.find((t) => t.id === id) || null });
    }
    if (method === 'POST' && /^\/api\/transactions\/[^/]+\/claim$/.test(path)) {
      return json({ status: 'ok', claim_id: 'claim-1' });
    }
    if (method === 'POST' && /^\/api\/transactions\/[^/]+\/split$/.test(path)) {
      return json({ status: 'ok', parts: (body?.parts || []).length });
    }

    if (method === 'POST' && path === '/api/categories') {
      const name = String(body?.name || '').toLowerCase();
      if (name && !state.categories.includes(name)) {
        state.categories.push(name);
        state.customCategories.push(name);
      }
      return json({ status: 'ok' });
    }
    if (method === 'DELETE' && path.startsWith('/api/categories/')) {
      const name = decodeURIComponent(path.split('/').pop());
      state.categories = state.categories.filter((c) => c !== name);
      state.customCategories = state.customCategories.filter((c) => c !== name);
      return json({ status: 'ok' });
    }
    if (method === 'GET' && path === '/api/categories') return json(state.categories);
    if (method === 'GET' && path === '/api/categories/custom') return json(state.customCategories);

    if (method === 'PATCH' && path.startsWith('/api/recurring/')) {
      const id = path.split('/').pop();
      state.recurring = state.recurring.map(
        (s) => (s.id === id ? { ...s, ...(body || {}) } : s));
      return json({ status: 'ok' });
    }
    if (method === 'DELETE' && path.startsWith('/api/recurring/')) {
      const id = path.split('/').pop();
      state.recurring = state.recurring.filter((s) => s.id !== id);
      return json({ status: 'ok' });
    }
    if (method === 'GET' && path === '/api/recurring') return json(state.recurring);

    if (method === 'PUT' && path === '/api/profile') {
      state.profile = { ...state.profile, ...(body || {}) };
      return json(state.profile);
    }
    if (method === 'GET' && path === '/api/profile') return json(state.profile);

    if (method === 'PUT' && path === '/api/settings') {
      state.settings = { ...state.settings, ...(body || {}) };
      return json(state.settings);
    }
    if (method === 'GET' && path === '/api/settings') return json(state.settings);

    if (method === 'GET' && path === '/api/gmail/ignored') return json(state.ignoredSenders);
    if (method === 'PUT' && path === '/api/gmail/ignored') {
      state.ignoredSenders = { excluded_senders: body?.excluded_senders || [] };
      return json(state.ignoredSenders);
    }

    /* ---- why one row is the way it is ----
       Replayed from a real answer rather than invented, because the panel that
       renders it reads a dozen keys and a hand-written stand-in is exactly how
       it came to read three the endpoint has never returned. Two were captured
       - a plain row and a paired one - and which is served follows the row. */
    if (method === 'GET' && path.startsWith('/api/rules/explain/')) {
      const id = path.split('/').pop();
      const t = state.transactions.find((x) => x.id === id);
      if (!t) return json({ detail: 'No such transaction' }, 404);
      const shape = clone(
        fixtures[t.is_internal_transfer ? 'explain:transfer' : 'explain:plain']);
      return json({
        ...shape,
        id,
        category: {
          ...shape.category,
          value: t.category,
          source: t.category_source,
          rule: t.category_rule || null,
          confidence: t.category_confidence,
        },
        direction: { ...shape.direction, value: t.direction },
      });
    }

    /* ---- dashboards: one board, whatever id is asked for ---- */
    // Literal paths first. The server declares `/dashboards/templates` and
    // `/dashboards/import` ahead of `/dashboards/{id}` for the same reason -
    // otherwise "templates" is read as a dashboard id.
    if (method === 'GET' && path === '/api/dashboards/templates') {
      return json(clone(fixtures['/api/dashboards/templates']));
    }
    if (method === 'GET' && path === '/api/dashboards') return json(state.dashboards);
    if (method === 'POST' && path === '/api/dashboards') {
      const board = {
        ...clone(fixtures['dashboard:detail']),
        id: `board-${state.dashboards.length + 1}`,
        name: body?.name || 'Untitled dashboard',
      };
      state.dashboards = [...state.dashboards, {
        id: board.id, name: board.name, description: '', widget_count: board.widgets.length,
        filters: board.filters, is_default: false, position: state.dashboards.length,
      }];
      state.boardDetail[board.id] = board;
      return json({ status: 'ok', id: board.id });
    }
    if (method === 'GET' && /^\/api\/dashboards\/[^/]+$/.test(path)) {
      const id = path.split('/').pop();
      return json(state.boardDetail[id] || { ...clone(fixtures['dashboard:detail']), id });
    }
    if (method === 'POST' && /^\/api\/dashboards\/[^/]+\/run$/.test(path)) {
      return json(clone(fixtures['dashboard:run']));
    }
    if (method === 'POST' && path === '/api/query') {
      return json(clone(fixtures['query:result']));
    }
    if (method === 'PUT' && /^\/api\/dashboards\/[^/]+\/layout$/.test(path)) {
      return json({ status: 'ok' });
    }
    if (method === 'POST' && /^\/api\/dashboards\/[^/]+\/(widgets|duplicate)$/.test(path)) {
      return json({ status: 'ok', id: 'w-new' });
    }
    if ((method === 'PUT' || method === 'DELETE')
        && /^\/api\/dashboards\/[^/]+(\/widgets\/[^/]+)?$/.test(path)) {
      if (method === 'DELETE' && /^\/api\/dashboards\/[^/]+$/.test(path)) {
        const id = path.split('/').pop();
        state.dashboards = state.dashboards.filter((b) => b.id !== id);
      }
      return json({ status: 'ok' });
    }

    /* ---- claims ---- */
    if (method === 'GET' && path === '/api/claims') return json(state.claims);
    if (method === 'POST' && /^\/api\/claims\/[^/]+\/settle$/.test(path)) {
      const id = path.split('/')[3];
      state.claims = state.claims.map((c) => (c.id === id ? {
        ...c,
        settled_amount: String(Number(c.settled_amount || 0) + Number(body?.amount || 0)),
        status: Number(body?.amount || 0) >= Number(c.amount) ? 'settled' : 'partial',
      } : c));
      return json({ status: 'ok' });
    }

    /* ---- position ---- */
    if (method === 'GET' && path === '/api/position') return json(state.position);
    if (method === 'POST' && path === '/api/position/items') {
      const item = { id: `item-${state.position.items.length + 1}`, ...(body || {}) };
      state.position = { ...state.position, items: [...state.position.items, item] };
      return json({ status: 'ok', item });
    }
    if (method === 'PATCH' && /^\/api\/position\/items\/[^/]+$/.test(path)) {
      const id = path.split('/').pop();
      state.position = {
        ...state.position,
        items: state.position.items.map((i) => (i.id === id ? { ...i, ...(body || {}) } : i)),
      };
      return json({ status: 'ok' });
    }
    if (method === 'DELETE' && /^\/api\/position\/items\/[^/]+$/.test(path)) {
      const id = path.split('/')[4];
      state.position = {
        ...state.position, items: state.position.items.filter((i) => i.id !== id),
      };
      return json({ status: 'ok' });
    }
    if (method === 'POST' && /^\/api\/position\/items\/[^/]+\/review$/.test(path)) {
      return json({ status: 'ok' });
    }
    if (method === 'POST' && (path === '/api/position/review' || path === '/api/position/seed')) {
      return json({ status: 'ok', added: 0 });
    }

    /* ---- the setup wizard's own writes ---- */
    if (method === 'POST' && path.startsWith('/api/onboarding')) {
      const session = fixtures['/api/auth/session'];
      const done = path.endsWith('/complete');
      return json({
        ...clone(fixtures['/api/onboarding']),
        user: { ...session.user, onboarded: done },
        step: done ? 'done' : (body?.step || 'identity'),
        complete: done,
      });
    }

    /* ---- the operator's view ----
       404 to anybody the server does not recognise as an admin, which is what
       the screen has to handle. Served only when the fixture session says the
       account is one. */
    if (method === 'GET' && path === '/api/admin/overview') {
      const session = routes['GET /api/auth/session'] || fixtures['/api/auth/session'];
      if (!session?.is_admin) return json({ detail: 'Not found' }, 404);
      return json(clone(fixtures['admin:overview']));
    }

    /* ---- jobs: a run that finishes on the second poll ---- */
    if (method === 'GET' && path.startsWith('/api/jobs/')) {
      const id = path.split('/').pop();
      const job = state.jobs[id] || { id, kind: 'test', active: false, status: 'done', progress: 1 };
      state.jobs[id] = { ...job, active: false };
      return json(job);
    }
    if (method === 'GET' && path === '/api/jobs') {
      return json({ jobs: Object.values(state.jobs), active_count: 0 });
    }

    /* ---- everything else is a document, replayed ---- */
    const withSearch = search ? `${path}?${search}` : path;
    if (Object.prototype.hasOwnProperty.call(fixtures, withSearch)) {
      return json(clone(fixtures[withSearch]));
    }
    if (Object.prototype.hasOwnProperty.call(fixtures, path)) {
      return json(clone(fixtures[path]));
    }
    /* Period-scoped documents: the fixture is the all-time answer, and the
       screens only need it to be present and shaped right. */
    const base = path.split('?')[0];
    const allTime = `${base}?preset=all`;
    if (Object.prototype.hasOwnProperty.call(fixtures, allTime)) {
      return json(clone(fixtures[allTime]));
    }

    // Anything else is a call the fake does not know about. Answering 404 is
    // what turns a mistyped path in the app into a failing test rather than a
    // silent `undefined`.
    return json({ detail: `no fake route for ${method} ${path}` }, 404);
  }

  const fetchImpl = vi.fn(async (input, init = {}) => {
    const url = new URL(String(input), 'http://localhost');
    const method = String(init.method || 'GET').toUpperCase();
    let body = null;
    if (typeof init.body === 'string') {
      try { body = JSON.parse(init.body); } catch { body = init.body; }
    } else if (init.body) {
      body = init.body;                                // FormData, for uploads
    }
    const record = {
      method, path: url.pathname, search: url.search.replace(/^\?/, ''), body,
    };
    calls.push(record);
    const response = handle(method, url.pathname, record.search, body);
    record.status = response.status;
    return response;
  });

  vi.stubGlobal('fetch', fetchImpl);
  clearCache();

  return {
    calls,
    state,
    fetchImpl,
    /** Every call to `path`, newest last. */
    callsTo: (path, method) => calls.filter(
      (c) => c.path === path && (!method || c.method === method)),
    /** The most recent call to `path`, or undefined. */
    lastCall: (path, method) => [...calls].reverse().find(
      (c) => c.path === path && (!method || c.method === method)),
  };
}

/* ── rendering a screen ──────────────────────────────────────────────────── */

/** The account the fixtures were captured as. */
export const USER_ID = fixtures['/api/auth/session'].user.id;

/**
 * Set display preferences the way the app stores them.
 *
 * Preferences are namespaced per account (core/storage.js), and which account
 * that is only becomes known once the session has been read - so a test that
 * writes the bare key sets a preference the app will never look at. Both keys
 * are written here so the value holds whenever a screen happens to read it.
 */
export function setPrefs(values) {
  const raw = JSON.stringify(values);
  window.localStorage.setItem('prism-prefs', raw);
  window.localStorage.setItem(`prism-prefs::${USER_ID}`, raw);
}

/* Nothing renders until the session has been read.
 *
 * This is what `app/Gate.jsx` does, and it matters to more than fidelity:
 * browser-local state is namespaced by the signed-in account, so a screen
 * mounted before the session arrives reads preferences out of the wrong
 * namespace - and its first request goes out against half-loaded state. */
function AfterAuth({ children }) {
  const { loading } = useAuth();
  return loading ? null : children;
}

/**
 * Render `ui` inside the providers the app gives every screen.
 *
 * `route` sets the address first, so a screen reading a query parameter (the
 * ledger's preset, the review mode, the open dashboard) sees it on its first
 * render rather than after a navigation.
 */
export function renderScreen(ui, { route = '/', ...options } = {}) {
  window.history.replaceState({}, '', route);
  return render(
    <AuthProvider>
      <RouterProvider>
        <ToastProvider>
          <AfterAuth>
            <PeriodProvider>
              <DrillProvider>{ui}</DrillProvider>
            </PeriodProvider>
          </AfterAuth>
        </ToastProvider>
      </RouterProvider>
    </AuthProvider>,
    options,
  );
}

/** Render without the period/drill providers, for the shell and the door. */
export function renderApp(ui, { route = '/', ...options } = {}) {
  window.history.replaceState({}, '', route);
  return render(
    <AuthProvider>
      <RouterProvider>
        <ToastProvider>{ui}</ToastProvider>
      </RouterProvider>
    </AuthProvider>,
    options,
  );
}
