import React, { useCallback, useEffect, useRef, useState } from 'react';
import CreditReport from './components/CreditReport';
import DataHub from './components/DataHub';
import Debt from './components/Debt';
import Ledger from './components/Ledger';
import Portfolio from './components/Portfolio';
import ReviewHub from './components/ReviewHub';
import Overview from './components/Overview';
import Profile from './components/Profile';
import Spending from './components/Spending';
import { Callout, Empty, ThemeToggle } from './components/ui';
import { api } from './lib';

import WorkflowNav from './components/WorkflowNav';
import Recurring from './components/Recurring';
import Settings from './components/Settings';
import MonthView from './components/MonthView';
import Explore from './components/explore/Explore';
import MailboxButton from './components/mailbox/MailboxButton';
import MailboxModal from './components/mailbox/MailboxModal';
import useMailbox from './components/mailbox/useMailbox';


// Ordered by how often they are used, not by when they were built. The five
// views added with the accounting model existed as components and render
// branches for a while but were never listed here, which meant there was no
// way to reach any of them.
// Eighteen tabs became eleven. Four of the removed ones - Savings, Cards, UPI
// and EMI - were the same transactions table behind different presets, and
// three more split file bookkeeping and triage across tabs that described the
// same work. What varied is now a control inside one tab, which is where a
// choice belongs when it changes a view rather than a subject.
const TABS = [
  ['overview', 'Overview'],
  ['month-view', 'Months'],
  ['spending', 'Spending'],
  ['recurring', 'Recurring'],
  ['review', 'Review'],
  ['ledger', 'Ledger'],
  ['debt', 'Debt'],
  ['credit', 'Credit report'],
  ['portfolio', 'Portfolio'],
  ['explore', 'Explore'],
  ['data', 'Data'],
  ['settings', 'Settings'],
];

//: Tabs that work without a parsed ledger behind them.
const ALWAYS_AVAILABLE = ['settings', 'data', 'credit', 'portfolio'];

export default function App() {
  const [data, setData] = useState(null);
  const [tab, setTab] = useState('overview');
  const [showProfile, setShowProfile] = useState(false);
  const [mailboxOpen, setMailboxOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [reviewCount, setReviewCount] = useState(0);
  const [theme, setTheme] = useState(
    () => localStorage.getItem('fa-theme')
      || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
  );

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('fa-theme', theme);
  }, [theme]);

  // True once anything has been shown. A refresh after that point must not
  // swap the whole of <main> for a spinner: doing so unmounts the tab the
  // user is looking at, and a panel that reports what a run just did loses
  // that report to the very refresh that proves the run worked.
  const shown = useRef(false);

  const load = useCallback(async () => {
    if (!shown.current) setLoading(true);
    try {
      const dashboard = await api.dashboard();
      setData(dashboard.status === 'ok' ? dashboard : null);
      setError(dashboard.status === 'stale' ? dashboard.message : null);
      // Badge on the Review tab. Failing to count is not worth surfacing -
      // the tab still works, it just shows no number.
      api.workflow()
        .then((w) => setReviewCount(w?.counts?.needs_review || 0))
        .catch(() => setReviewCount(0));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
      shown.current = true;
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // One poller for the whole app. Held here rather than inside the modal
  // because the header button needs the same answer, and because an import
  // that finishes while the modal is closed still has to refresh the ledger.
  const mailbox = useMailbox({ open: mailboxOpen, onImported: load });

  const hasData = Boolean(data?.analysis?.totals);

  return (
    <div className="app">
      <header className="header">
        <div className="brand" style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '18px', fontWeight: 600, color: 'var(--text-1)' }}>
          <img src="/favicon.svg" alt="Prism Logo" style={{ width: 20, height: 20, display: 'block' }} />
          Prism
        </div>

        <nav className="tabs" role="tablist">
          {TABS.map(([key, label]) => {
            const isDataTab = !ALWAYS_AVAILABLE.includes(key);
            if (!hasData && isDataTab) return null;
            return (
              <button
                key={key}
                className="tab"
                role="tab"
                aria-selected={tab === key}
                onClick={() => setTab(key)}
              >
                {label}
                {key === 'review' && reviewCount > 0 && (
                  <span
                    className="chip warn"
                    style={{ marginLeft: 6, padding: '0 6px' }}
                  >
                    {reviewCount}
                  </span>
                )}
              </button>
            );
          })}
        </nav>

        <div className="header-spacer" />

        <MailboxButton mailbox={mailbox} onOpen={() => setMailboxOpen(true)} />
        <button className="btn" onClick={() => setShowProfile(true)}>Profile</button>
        <ThemeToggle theme={theme} onToggle={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))} />
      </header>

      <main className="main">
        {showProfile ? (
          <Profile onSaved={() => setShowProfile(false)} />
        ) : loading ? (
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', padding: 40 }}>
            <div className="spinner" /> Loading…
          </div>
        ) : !hasData && !ALWAYS_AVAILABLE.includes(tab) ? (
          <>
            {/* One way in. The drop zone that used to live here was a second
                one - a file dropped on this page skipped the review that every
                other import goes through, so the two routes could not both be
                right. Uploading is now the first step of the same wizard, and
                this page's job is to point at it. */}
            <div style={{ maxWidth: 620, margin: '0 auto', textAlign: 'center',
              padding: '40px 0' }}>
              <h1 style={{ fontSize: 26, fontWeight: 660, letterSpacing: '-.6px',
                margin: '0 0 10px' }}>
                Understand where your money actually goes
              </h1>
              <p style={{ color: 'var(--text-2)', fontSize: 15, lineHeight: 1.6,
                margin: '0 0 20px' }}>
                Read your bank, card, loan and investment statements — from your
                mailbox or from files you have. Every figure is reconciled
                against the balances your bank printed, and nothing counts until
                you have seen what was read.
              </p>
              {error && <Callout tone="warn" style={{ marginBottom: 16 }}>{error}</Callout>}
              <button className="btn primary" style={{ fontSize: 14, padding: '8px 18px' }}
                onClick={() => setMailboxOpen(true)}>
                Import statements
              </button>
              <p style={{ color: 'var(--text-3)', fontSize: 13, marginTop: 16 }}>
                Scan Gmail or add files from this computer — both start in the
                same place. Password-protected PDFs open automatically once your
                details are in{' '}
                <button className="btn" style={{ padding: '2px 8px', fontSize: 12 }}
                  onClick={() => setShowProfile(true)}>Profile</button>.
              </p>
            </div>
          </>
        ) : (
          <>
            {error && <Callout tone="warn">{error}</Callout>}
            {hasData && <WorkflowNav onNavigate={setTab} />}
            {tab === 'overview' && <Overview data={data} />}
            {tab === 'month-view' && <MonthView data={data} />}
            {tab === 'spending' && <Spending data={data} />}
            {tab === 'recurring' && <Recurring />}
            {tab === 'review' && <ReviewHub />}
            {tab === 'ledger' && <Ledger data={data} />}
            {tab === 'debt' && <Debt data={data} />}
            {tab === 'credit' && <CreditReport accounts={data?.accounts || []}
              onImport={() => setMailboxOpen(true)} />}
            {tab === 'portfolio' && <Portfolio onImport={() => setMailboxOpen(true)} />}
            {tab === 'explore' && <Explore />}
            {tab === 'data' && <DataHub data={data}
              onImport={() => setMailboxOpen(true)} />}
            {tab === 'settings' && <Settings onLedgerChanged={load} />}
          </>
        )}
      </main>

      <MailboxModal mailbox={mailbox} open={mailboxOpen}
        onClose={() => setMailboxOpen(false)} onUploaded={load} />
    </div>
  );
}
