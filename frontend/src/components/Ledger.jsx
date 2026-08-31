import React, { useMemo, useState } from 'react';
import { compact, money } from '../lib';
import EmiPayments from './EmiPayments';
import TransactionsTable from './TransactionsTable';
import { Empty } from './ui';

/* Every transaction, with the scoped views as presets rather than as tabs.
 *
 * Savings, Cards, UPI and EMI Payments used to be four separate tabs. All four
 * were the same TransactionsTable with a filter applied before it - four
 * entries in the nav, four files, one component. The filters are worth having;
 * the tabs were not, because the thing they varied is exactly what this table
 * already lets you change.
 *
 * Each preset still narrows to the same rows its tab did, so nothing is lost
 * except the need to guess which of four places a transaction is hiding in. */

const SAVINGS_TYPES = new Set(['savings', 'current', 'wallet']);

const VIEWS = [
  {
    key: 'all',
    label: 'All',
    title: 'Transactions',
    hint: 'Every account, every filter available.',
    accounts: (all) => all,
  },
  {
    key: 'savings',
    label: 'Bank accounts',
    title: 'Savings & current accounts',
    hint: 'Money actually in the bank. Cards and loans have their own views.',
    accounts: (all) => all.filter((a) => SAVINGS_TYPES.has(a.account_type)),
    empty: 'No savings or current accounts found.',
  },
  {
    key: 'cards',
    label: 'Cards',
    title: 'Card transactions',
    hint: 'Credit cards only, with a UPI/other split - a lot of Indian card '
      + 'spend is routed through UPI rather than a swipe.',
    accounts: (all) => all.filter((a) => a.account_type === 'credit_card'),
    props: { showRailToggle: true },
    empty: 'No credit cards found.',
  },
  {
    key: 'upi',
    label: 'UPI',
    title: 'UPI transactions',
    hint: 'UPI across every account. It is a payment rail, not an account '
      + 'type, and most of it happens straight off a bank account.',
    accounts: (all) => all,
    props: { fixedRail: 'upi' },
  },
  {
    key: 'emi',
    label: 'EMI',
    title: 'EMI payments',
    hint: 'What was actually paid, month by month - which can differ from the '
      + 'nominal EMI when there is a part-payment or a missed month.',
    accounts: (all) => all,
    props: { fixedCategory: 'emi' },
  },
];

const STORAGE_KEY = 'fa-ledger-view';

export default function Ledger({ data }) {
  const [view, setView] = useState(
    () => localStorage.getItem(STORAGE_KEY) || 'all');

  const accounts = data.accounts || [];
  const active = VIEWS.find((v) => v.key === view) || VIEWS[0];
  const scoped = useMemo(() => active.accounts(accounts), [active, accounts]);

  const pick = (key) => {
    setView(key);
    try { localStorage.setItem(STORAGE_KEY, key); } catch { /* private mode */ }
  };

  return (
    <>
      <div className="seg" style={{ marginBottom: 12 }}>
        {VIEWS.map((one) => {
          // A view with nothing behind it is shown but marked, rather than
          // hidden: a missing tab reads as a bug, a greyed one reads as "you
          // have not imported a card statement yet".
          const count = one.accounts(accounts).length;
          return (
            <button
              key={one.key}
              className={`seg-btn ${view === one.key ? 'active' : ''}`}
              onClick={() => pick(one.key)}
              title={one.hint}
              style={{ opacity: count ? 1 : 0.55 }}
            >
              {one.label}
            </button>
          );
        })}
      </div>

      <div className="xp-hint" style={{ textTransform: 'none', marginBottom: 12 }}>
        {active.hint}
      </div>

      {/* The loan cards used to live in their own EMI Payments tab. They belong
          with the payments they explain, not in a tab of their own. */}
      {view === 'emi' && <EmiPayments data={data} cardsOnly />}

      {!scoped.length && active.empty ? (
        <Empty title={active.empty}>
          Import a statement, or scan your mailbox, to see them here.
        </Empty>
      ) : (
        <TransactionsTable
          // Keyed by preset so switching remounts the table. Without this
          // React reuses one instance and its account selection survives the
          // switch - pick Cards, then EMI, and the table is still filtered to
          // two card accounts while showing every account in its picker, so a
          // view with 28 matching rows renders "no transactions match".
          key={active.key}
          accounts={scoped}
          title={active.title}
          emptyHint="Try a different account, or clear the filters."
          {...(active.props || {})}
        />
      )}
    </>
  );
}
