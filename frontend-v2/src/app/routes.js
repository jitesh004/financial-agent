/* Every destination in the app, in one table.
 *
 * The navigation, the command palette, the page title and the router all read
 * this - so adding a screen is one entry here and a lazy import, and there is
 * no way for the rail to offer something the router cannot reach.
 *
 * `needsLedger` marks the screens that only mean something once statements
 * have been parsed. The ones without it work from an empty install on
 * purpose: Rules describes what the app WOULD do, so it is at its most useful
 * before anything is imported, and somebody who has not connected a bank can
 * still write down what they owe in Position - a more useful first five
 * minutes than an empty dashboard.
 */

import { lazy } from 'react';

export const GROUPS = [
  { key: 'money', label: 'Money' },
  { key: 'accounts', label: 'Accounts' },
  { key: 'rows', label: 'Transactions' },
  { key: 'manage', label: 'Manage' },
];

export const ROUTES = [
  {
    path: '/', key: 'overview', group: 'money', label: 'Overview', icon: 'overview',
    title: 'Overview', needsLedger: true, period: true,
    blurb: 'Everything at a glance, for the period you are looking at.',
    load: () => import('../screens/Overview'),
  },
  {
    /* Second in Money, ahead of the screens that show what happened, because
       what an agent answers is not "what happened" - it is the question you
       would have had to know to ask. A screen you only find by exhausting the
       others is a screen nobody finds. */
    path: '/agents', key: 'agents', group: 'money', label: 'Agents', icon: 'sparkles',
    title: 'Agents', needsLedger: true,
    blurb: 'A model reads your ledger and answers a hard question about it.',
    load: () => import('../screens/Agents'),
  },
  {
    path: '/budget', key: 'budget', group: 'money', label: 'Budget', icon: 'wallet',
    title: 'A typical month', needsLedger: true, period: true,
    blurb: 'What a month costs before any choices are made, and what that leaves.',
    load: () => import('../screens/Budget'),
  },
  {
    path: '/months', key: 'months', group: 'money', label: 'Months', icon: 'calendar',
    title: 'Months', needsLedger: true,
    blurb: 'The period, every account, one month at a time.',
    load: () => import('../screens/Months'),
  },
  {
    path: '/spending', key: 'spending', group: 'money', label: 'Spending', icon: 'trending',
    title: 'Spending', needsLedger: true, period: true,
    blurb: 'Where the money went, by category, group and merchant.',
    load: () => import('../screens/Spending'),
  },
  {
    path: '/recurring', key: 'recurring', group: 'money', label: 'Recurring', icon: 'repeat',
    title: 'Recurring', needsLedger: true, period: true,
    blurb: 'Every charge that repeats, and the rows it was inferred from.',
    load: () => import('../screens/Recurring'),
  },
  {
    path: '/forecast', key: 'forecast', group: 'money', label: 'Forecast', icon: 'gauge',
    title: 'What happens next', needsLedger: true,
    blurb: 'Projected balance, as a range rather than a number.',
    load: () => import('../screens/Forecast'),
  },

  {
    /* First in Accounts, because it is the only one of these that is not
       merely what the imports happened to cover. It is what you have checked
       and confirmed, including the loan no statement mentions. */
    path: '/position', key: 'position', group: 'accounts', label: 'Position', icon: 'scales',
    title: 'Position',
    blurb: 'What you have checked yourself, aged forward from the day you confirmed it.',
    load: () => import('../screens/Position'),
  },
  {
    path: '/debt', key: 'debt', group: 'accounts', label: 'Debt', icon: 'credit',
    title: 'Debt', needsLedger: true,
    blurb: 'Loans, amortization, payoff dates and card utilisation.',
    load: () => import('../screens/Debt'),
  },
  {
    path: '/credit', key: 'credit', group: 'accounts', label: 'Credit report', icon: 'shield',
    title: 'Credit report',
    blurb: 'What lenders report about you, laid against what this app knows.',
    load: () => import('../screens/Credit'),
  },
  {
    path: '/portfolio', key: 'portfolio', group: 'accounts', label: 'Portfolio', icon: 'briefcase',
    title: 'Portfolio',
    blurb: 'What you own, valued at the NAV the statement printed.',
    load: () => import('../screens/Portfolio'),
  },
  {
    path: '/owed', key: 'owed', group: 'accounts', label: 'Owed', icon: 'hand',
    title: 'Money owed', needsLedger: true,
    blurb: 'Expenses that were never really yours, until they come back.',
    load: () => import('../screens/Owed'),
  },

  {
    path: '/ledger', key: 'ledger', group: 'rows', label: 'Ledger', icon: 'rows',
    title: 'Ledger', needsLedger: true, period: true,
    blurb: 'Every transaction, with every filter the app can apply.',
    load: () => import('../screens/Ledger'),
  },
  {
    path: '/review', key: 'review', group: 'rows', label: 'Review', icon: 'inbox',
    title: 'Review', needsLedger: true, period: true, badge: 'review',
    blurb: 'Everything the pipeline could not settle on its own.',
    load: () => import('../screens/Review'),
  },
  {
    path: '/explore', key: 'explore', group: 'rows', label: 'Explore', icon: 'compass',
    title: 'Explore', needsLedger: true,
    blurb: 'Dashboards you build. Every widget is a saved question.',
    load: () => import('../screens/Explore'),
  },

  {
    path: '/data', key: 'data', group: 'manage', label: 'Data', icon: 'database',
    title: 'Data',
    blurb: 'Coverage, every file ever attempted, snapshots and clearing.',
    load: () => import('../screens/Data'),
  },
  {
    path: '/rules', key: 'rules', group: 'manage', label: 'Rules', icon: 'book',
    title: 'Rules',
    blurb: 'Every rule the app runs on your documents, readable.',
    load: () => import('../screens/Rules'),
  },
  {
    path: '/settings', key: 'settings', group: 'manage', label: 'Settings', icon: 'settings',
    title: 'Settings',
    blurb: 'Categories, display, your account, and the model switch.',
    load: () => import('../screens/Settings'),
  },
  {
    /* Only for an address listed in FA_ADMIN_EMAILS on the server. The
       endpoint answers 404 to everyone else regardless, so hiding this is
       convenience, not the control. */
    path: '/admin', key: 'admin', group: 'manage', label: 'Admin', icon: 'users',
    title: 'This deployment', adminOnly: true,
    blurb: 'Who is on this deployment, and how much they use it.',
    load: () => import('../screens/Admin'),
  },

  /* Reachable, but not in the rail: these are opened from the account menu or
     from a link, and listing them beside the screens you visit daily would be
     four more things to read past every time. */
  {
    path: '/profile', key: 'profile', label: 'Your details', icon: 'user', hidden: true,
    title: 'Your details',
    blurb: 'The details that open your own password-protected statements.',
    load: () => import('../screens/Profile'),
  },
];

export const BY_PATH = Object.fromEntries(ROUTES.map((r) => [r.path, r]));
export const BY_KEY = Object.fromEntries(ROUTES.map((r) => [r.key, r]));

export const LAZY = Object.fromEntries(ROUTES.map((r) => [r.key, lazy(r.load)]));

export function routeFor(path) {
  return BY_PATH[path] || BY_PATH[path.replace(/\/+$/, '')] || null;
}
