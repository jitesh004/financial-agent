/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Route Registry
   Every screen destination, metadata, icon mapping, and lazy loader.
   ──────────────────────────────────────────────────────────────────────── */

import { lazy } from 'react';

export const GROUPS = [
  { key: 'money', label: 'Intelligence & Cashflow' },
  { key: 'accounts', label: 'Wealth & Balance Sheet' },
  { key: 'rows', label: 'Ledger & Registers' },
  { key: 'manage', label: 'System & Configuration' },
];

export const ROUTES = [
  /* Intelligence & Cashflow */
  {
    path: '/', key: 'overview', group: 'money', label: 'Overview', icon: 'overview',
    title: 'Executive Overview', needsLedger: true, period: true,
    blurb: 'Executive financial health, money stream, and cashflow pulse.',
    load: () => import('../screens/Overview'),
  },
  {
    path: '/ask', key: 'ask', group: 'money', label: 'Ask', icon: 'question',
    title: 'Ask Your Ledger', needsLedger: true,
    blurb: 'Ask anything about your money and read the queries behind the answer.',
    load: () => import('../screens/Chat'),
  },
  {
    path: '/agents', key: 'agents', group: 'money', label: 'Copilot Agents', icon: 'sparkles',
    title: 'AI Financial Agents', needsLedger: true,
    blurb: 'Specialized LangGraph models auditing leaks, resilience, debt and taxes.',
    load: () => import('../screens/Agents'),
  },
  {
    path: '/budget', key: 'budget', group: 'money', label: 'Budget & Scenarios', icon: 'wallet',
    title: 'Monthly Budget', needsLedger: true, period: true,
    blurb: '50/30/20 compliance, category headway, and expense cut simulator.',
    load: () => import('../screens/Budget'),
  },
  {
    path: '/months', key: 'months', group: 'money', label: 'Months Matrix', icon: 'calendar',
    title: 'Monthly Matrix', needsLedger: true,
    blurb: 'Multi-month matrix comparison across all accounts and categories.',
    load: () => import('../screens/Months'),
  },
  {
    path: '/spending', key: 'spending', group: 'money', label: 'Spending Analytics', icon: 'trending',
    title: 'Spending Breakdown', needsLedger: true, period: true,
    blurb: 'Category breakdown, Pareto merchant rankings, and discretionary split.',
    load: () => import('../screens/Spending'),
  },
  {
    path: '/recurring', key: 'recurring', group: 'money', label: 'Recurring & Leaks', icon: 'repeat',
    title: 'Recurring Charges', needsLedger: true, period: true,
    blurb: 'Subscriptions, EMIs, insurance cadences, and annual leak auditing.',
    load: () => import('../screens/Recurring'),
  },
  {
    path: '/forecast', key: 'forecast', group: 'money', label: 'Forecast Cones', icon: 'gauge',
    title: 'Cashflow Forecast', needsLedger: true,
    blurb: '90-day balance trajectories with Monte Carlo confidence cones.',
    load: () => import('../screens/Forecast'),
  },

  /* Wealth & Balance Sheet */
  {
    path: '/position', key: 'position', group: 'accounts', label: 'Net Worth', icon: 'scales',
    title: 'Financial Position',
    blurb: 'Net worth balance sheet, asset allocation, and reviewed balances.',
    load: () => import('../screens/Position'),
  },
  {
    path: '/debt', key: 'debt', group: 'accounts', label: 'Debt & Loans', icon: 'credit',
    title: 'Debt & Amortization', needsLedger: true,
    blurb: 'Amortization schedules, card limits, and Snowball vs Avalanche simulator.',
    load: () => import('../screens/Debt'),
  },
  {
    path: '/credit', key: 'credit', group: 'accounts', label: 'Credit Bureau', icon: 'shield',
    title: 'Credit Report',
    blurb: 'Credit score audit, inquiry history, and account reconciliation.',
    load: () => import('../screens/Credit'),
  },
  {
    path: '/portfolio', key: 'portfolio', group: 'accounts', label: 'Portfolio & NAV', icon: 'briefcase',
    title: 'Holdings Portfolio',
    blurb: 'Mutual funds, equities, PF/NPS holdings valued at printed NAV.',
    load: () => import('../screens/Portfolio'),
  },
  {
    path: '/owed', key: 'owed', group: 'accounts', label: 'Money Owed', icon: 'hand',
    title: 'Claims & Reimbursements', needsLedger: true,
    blurb: 'Split expenses, pending reimbursements, and claim settlements.',
    load: () => import('../screens/Owed'),
  },

  /* Ledger & Registers */
  {
    path: '/ledger', key: 'ledger', group: 'rows', label: 'Power Terminal', icon: 'terminal',
    title: 'Transaction Ledger', needsLedger: true, period: true,
    blurb: 'High-density terminal with faceted filters, split editor & bulk actions.',
    load: () => import('../screens/Ledger'),
  },
  {
    path: '/review', key: 'review', group: 'rows', label: 'Review Inbox', icon: 'inbox',
    title: 'Review Inbox', needsLedger: true, period: true, badge: 'review',
    blurb: 'Items requiring human review: unknown merchants, mismatches, and anomalies.',
    load: () => import('../screens/Review'),
  },
  {
    path: '/explore', key: 'explore', group: 'rows', label: 'Custom Dashboards', icon: 'compass',
    title: 'Explore & Build', needsLedger: true,
    blurb: 'Custom queries, dynamic widget builder, and modular analytics boards.',
    load: () => import('../screens/Explore'),
  },

  /* System & Configuration */
  {
    path: '/data', key: 'data', group: 'manage', label: 'Data & Files', icon: 'database',
    title: 'Data Management',
    blurb: 'Statement coverage matrix, imported file registry, and snapshot wipes.',
    load: () => import('../screens/Data'),
  },
  {
    path: '/llm-usage', key: 'llm-usage', group: 'manage', label: 'Model Usage',
    icon: 'sparkles',
    title: 'Model Usage',
    blurb: 'Every model request: tokens, cost, which key, and what failed.',
    load: () => import('../screens/LlmUsage'),
  },
  {
    path: '/rules', key: 'rules', group: 'manage', label: 'Rules Engine', icon: 'book',
    title: 'Rules & Heuristics',
    blurb: 'Categorization rules, transfer detectors, and interactive rule tester.',
    load: () => import('../screens/Rules'),
  },
  {
    path: '/settings', key: 'settings', group: 'manage', label: 'Settings', icon: 'settings',
    title: 'Preferences',
    blurb: 'Display settings, custom categories, LLM model choice, and demo switch.',
    load: () => import('../screens/Settings'),
  },
  {
    path: '/admin', key: 'admin', group: 'manage', label: 'Deployment Admin', icon: 'users',
    title: 'System Admin', adminOnly: true,
    blurb: 'Active users, parsing volume, and system metrics.',
    load: () => import('../screens/Admin'),
  },
  {
    path: '/profile', key: 'profile', label: 'Security & PDF Passwords', icon: 'user', hidden: true,
    title: 'Decryption Details',
    blurb: 'PAN, DOB, phone credentials to unlock encrypted PDF statements.',
    load: () => import('../screens/Profile'),
  },
];

export const BY_PATH = Object.fromEntries(ROUTES.map((r) => [r.path, r]));
export const BY_KEY = Object.fromEntries(ROUTES.map((r) => [r.key, r]));
export const LAZY = Object.fromEntries(ROUTES.map((r) => [r.key, lazy(r.load)]));

export function routeFor(path) {
  return BY_PATH[path] || BY_PATH[path.replace(/\/+$/, '')] || null;
}
