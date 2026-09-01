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
import AccountMenu from './components/AccountMenu';
import { api } from './lib';

import WorkflowNav from './components/WorkflowNav';
import Recurring from './components/Recurring';
import Rules from './components/Rules';
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
/* Thirteen destinations, grouped by the question they answer.
 *
 * They were a flat strip, and at a 1264px laptop width nine of them were off
 * the edge - including Review with 67 rows waiting. The strip scrolled, but
 * with a hidden scrollbar and a fade mask, so the hidden tabs did not read as
 * cut off. They read as absent.
 *
 * Grouping is what fixes that rather than finding more room: a list that has
 * to be scrolled to be read is a list nobody can hold in their head, and this
 * one was going to keep growing.
 *
 * Both rows live on ONE line - see the measurement in .nav-groups. The header
 * has ~919px of usable width and the two rows together need about 650, so the
 * grouped nav costs LESS space than the flat strip it replaces, not more.
 */
const GROUPS = [
  ['money', 'Money', [
    ['overview', 'Overview'],
    ['month-view', 'Months'],
    ['spending', 'Spending'],
    ['recurring', 'Recurring'],
  ]],
  ['accounts', 'Accounts', [
    ['debt', 'Debt'],
    ['credit', 'Credit report'],
    ['portfolio', 'Portfolio'],
  ]],
  ['rows', 'Transactions', [
    ['ledger', 'Ledger'],
    ['review', 'Review'],
    ['explore', 'Explore'],
  ]],
  ['manage', 'Manage', [
    ['data', 'Data'],
    ['rules', 'Rules'],
    ['settings', 'Settings'],
  ]],
];

//: tab key -> the group holding it, so the active group follows the tab
//: rather than being tracked separately and drifting out of step with it.
const GROUP_OF = Object.fromEntries(
  GROUPS.flatMap(([g, , members]) => members.map(([key]) => [key, g])));

//: Tabs that work without a parsed ledger behind them. Rules is one of
//: them by nature - it describes what the app WOULD do, so it is at its
//: most useful before anything has been imported.
const ALWAYS_AVAILABLE = ['settings', 'data', 'rules', 'credit', 'portfolio'];

/* Navigation for a viewport too narrow to hold it.
 *
 * The inline nav cannot shrink below its own content: at 375px its members
 * row alone is 326px, and with the brand and the header controls beside it
 * the page scrolled sideways and the tabs sat underneath the Mailbox and
 * Profile buttons. Wrapping does not help - there is no width to wrap into.
 *
 * A drawer is the honest answer at that size: the whole map at once, in a
 * column, where vertical space is the thing there is plenty of.
 */
function NavDrawer({ groups, tab, reviewCount, onPick, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    // The page behind must not scroll while a full-height panel is over it.
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <nav
        className="drawer"
        aria-label="Sections"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="drawer-head">
          <span className="drawer-title">Go to</span>
          <button className="btn icon" aria-label="Close menu" onClick={onClose}>
            ✕
          </button>
        </div>

        {groups.map(([group, label, members]) => (
          <div key={group} className="drawer-group">
            <div className="drawer-group-label">{label}</div>
            {members.map(([key, name]) => (
              <button
                key={key}
                className="drawer-item"
                aria-current={tab === key ? 'page' : undefined}
                onClick={() => onPick(key)}
              >
                {name}
                {key === 'review' && reviewCount > 0 && (
                  <span className="chip warn nav-count">{reviewCount}</span>
                )}
              </button>
            ))}
          </div>
        ))}
      </nav>
    </div>
  );
}

export default function App({ openImport = false, onImportOpened }) {
  const [data, setData] = useState(null);
  const [tab, setTab] = useState('overview');
  // The member last used in each group. Bouncing between Ledger and Spending
  // should not make you re-navigate every time.
  const [lastInGroup, setLastInGroup] = useState({});
  const [menuOpen, setMenuOpen] = useState(false);
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

  // The wizard's "Import statements" button finishes onboarding and asks the
  // app to open the import flow, rather than leaving someone who just said
  // "yes, bring my statements in" looking at an empty dashboard.
  useEffect(() => {
    if (openImport) {
      setMailboxOpen(true);
      onImportOpened?.();
    }
  }, [openImport, onImportOpened]);

  // One poller for the whole app. Held here rather than inside the modal
  // because the header button needs the same answer, and because an import
  // that finishes while the modal is closed still has to refresh the ledger.
  const mailbox = useMailbox({ open: mailboxOpen, onImported: load });

  const hasData = Boolean(data?.analysis?.totals);

  // A group with nothing available in it is not shown at all: before an
  // import, Money and Transactions have no members, and an empty group is a
  // dead end wearing the same clothes as a live one.
  const visibleGroups = GROUPS
    .map(([group, label, members]) => [
      group, label,
      members.filter(([key]) => hasData || ALWAYS_AVAILABLE.includes(key)),
    ])
    .filter(([, , members]) => members.length > 0);

  // The group holding the current tab - unless that group is not on screen,
  // which is the state a fresh install starts in: `tab` defaults to Overview,
  // Money has no members without a ledger, and following it blindly rendered
  // two groups with no members and nothing selected.
  const tabGroup = GROUP_OF[tab];
  const activeGroup = visibleGroups.some(([group]) => group === tabGroup)
    ? tabGroup : visibleGroups[0]?.[0];
  const activeMembers =
    visibleGroups.find(([group]) => group === activeGroup)?.[2] || [];

  useEffect(() => {
    if (GROUP_OF[tab]) {
      setLastInGroup((prev) => ({ ...prev, [GROUP_OF[tab]]: tab }));
    }
  }, [tab]);

  // Clicking a group always lands somewhere - on the member you were last
  // on there, or its first.
  const openGroup = (group, members) => {
    const remembered = lastInGroup[group];
    const target = members.some(([key]) => key === remembered)
      ? remembered : members[0]?.[0];
    if (target) setTab(target);
  };

  return (
    <div className="app">
      <header className="header">
        <button
          className="btn icon nav-toggle"
          aria-label="Menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen(true)}
        >
          ☰
        </button>

        <div className="brand" style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '18px', fontWeight: 600, color: 'var(--text-1)' }}>
          <img src="/favicon.svg" alt="Prism Logo" style={{ width: 20, height: 20, display: 'block' }} />
          Prism
        </div>

        {/* Groups, then the active group's members, on one row. The
            divider is the only chrome between them. */}
        <nav className="nav" aria-label="Sections">
          <div className="nav-groups" role="tablist" aria-label="Section">
            {visibleGroups.map(([group, label, members]) => {
              const count = members.reduce(
                (n, [key]) => n + (key === 'review' ? reviewCount : 0), 0);
              return (
                <button
                  key={group}
                  className="tab group"
                  role="tab"
                  aria-selected={activeGroup === group}
                  onClick={() => openGroup(group, members)}
                >
                  {label}
                  {/* A count on a hidden child would be invisible, which is
                      the whole failure being fixed - so it surfaces on the
                      group until you open it. */}
                  {count > 0 && activeGroup !== group && (
                    <span className="chip warn nav-count">{count}</span>
                  )}
                </button>
              );
            })}
          </div>

          <div className="nav-divider" aria-hidden="true" />

          <div className="nav-members" role="tablist" aria-label="View">
            {activeMembers.map(([key, label]) => (
              <button
                key={key}
                className="tab"
                role="tab"
                aria-selected={tab === key}
                onClick={() => setTab(key)}
              >
                {label}
                {key === 'review' && reviewCount > 0 && (
                  <span className="chip warn nav-count">{reviewCount}</span>
                )}
              </button>
            ))}
          </div>
        </nav>


        <MailboxButton mailbox={mailbox} onOpen={() => setMailboxOpen(true)} />
        <ThemeToggle theme={theme} onToggle={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))} />
        {/* Profile moved inside the account menu: it is one of several things
            that belong to "you" rather than to the ledger, and a header with a
            button for each of them stops fitting on a laptop. */}
        <AccountMenu onProfile={() => setShowProfile(true)} />

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
                same place. Password-protected PDFs open automatically once{' '}
                <button className="btn" style={{ padding: '2px 8px', fontSize: 12 }}
                  onClick={() => setShowProfile(true)}>your details</button>{' '}
                are filled in.
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
            {tab === 'rules' && <Rules />}
            {tab === 'settings' && <Settings onLedgerChanged={load} />}
          </>
        )}
      </main>

      {/* Rendered outside <header> on purpose. The header sets
          backdrop-filter, which makes it a containing block for
          position:fixed descendants - a drawer inside it resolved `inset: 0`
          against the header and rendered as a 55px strip. */}
      {menuOpen && (
        <NavDrawer
          groups={visibleGroups}
          tab={tab}
          reviewCount={reviewCount}
          onPick={(key) => { setTab(key); setMenuOpen(false); }}
          onClose={() => setMenuOpen(false)}
        />
      )}

      <MailboxModal mailbox={mailbox} open={mailboxOpen}
        onClose={() => setMailboxOpen(false)} onUploaded={load} />
    </div>
  );
}
