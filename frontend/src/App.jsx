import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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

import { SetupButton, SetupPanel, useSetupStatus } from './components/SetupStatus';
import Recurring from './components/Recurring';
import Rules from './components/Rules';
import Settings from './components/Settings';
import MonthView from './components/MonthView';
import Budget from './components/Budget';
import Admin from './components/Admin';
import DemoBanner from './components/DemoBanner';
import Explore from './components/explore/Explore';
import Agents from './components/agents/Agents';
import Position from './components/position/Position';
import MailboxButton from './components/mailbox/MailboxButton';
import MailboxModal from './components/mailbox/MailboxModal';
import useMailbox from './components/mailbox/useMailbox';
import PeriodPicker, { PeriodEmpty } from './components/PeriodPicker';
import { PeriodProvider, usePeriod } from './period';
import { DrillProvider } from './drill';
import { useAuth } from './auth';
import { useTheme } from './theme';


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
    /* Second in Money, ahead of the tabs that show what happened, because
       what an agent answers is not "what happened" - it is the question you
       would have had to know to ask. A screen you only find by exhausting
       the others is a screen nobody finds. */
    ['agents', 'Agents'],
    /* Second, deliberately. "What does a month cost me, and what does it
       leave" is the question asked most often after "what came in", and it
       was previously answerable only by reading three other tabs and doing
       the arithmetic by hand. */
    ['budget', 'Budget'],
    ['month-view', 'Months'],
    ['spending', 'Spending'],
    ['recurring', 'Recurring'],
  ]],
  ['accounts', 'Accounts', [
    /* First in Accounts, because it is the only one of these that is not
       merely what the imports happened to cover. It is what the user has
       checked and confirmed, including the loan no statement mentions -
       which makes it the thing the other three should be read against. */
    ['position', 'Position'],
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
    /* Only for an address listed in FA_ADMIN_EMAILS on the server - see
       ADMIN_ONLY below. The endpoint behind it answers 404 to everyone else
       regardless, so hiding the tab is convenience, not the control. */
    ['admin', 'Admin'],
  ]],
];

//: Tabs that exist only for whoever runs the deployment.
const ADMIN_ONLY = ['admin'];

//: tab key -> the group holding it, so the active group follows the tab
//: rather than being tracked separately and drifting out of step with it.
const GROUP_OF = Object.fromEntries(
  GROUPS.flatMap(([g, , members]) => members.map(([key]) => [key, g])));

//: Tabs that work without a parsed ledger behind them. Rules is one of
//: them by nature - it describes what the app WOULD do, so it is at its
//: most useful before anything has been imported.
const ALWAYS_AVAILABLE = ['settings', 'data', 'rules', 'credit', 'portfolio',
  'admin',
  /* Reachable before anything is imported, deliberately. Somebody who has
     not connected a bank yet can still write down what they owe, and that
     is a more useful first five minutes than an empty dashboard. */
  'position'];

/* Tabs the period control applies to, and therefore appears on.
 *
 * Not every tab has a period. A loan's amortization runs from today to
 * payoff; a credit report and a holdings statement are photographs of one
 * moment. Showing a range picker over those would imply it changes something,
 * which is worse than not offering it - and Explore carries its own per-board
 * range, because a board is a saved question about a period of its own.
 *
 * Months is the interesting exclusion. It reads the same period as everything
 * else, but it already HAS a period control - a strip of every month there is
 * data for, which is the more direct way to say "that one" - so a second one
 * above it would be two controls over one piece of state.
 */
const PERIOD_TABS = ['overview', 'spending', 'recurring', 'ledger', 'review',
  'budget'];

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

/* The period lives above the app so every tab reads the same one, and so it
   survives switching tabs - which is the whole point of a period that belongs
   to the app rather than to a panel. */
export default function App(props) {
  return (
    <PeriodProvider>
      {/* Any panel can open the rows behind a figure, so the sheet that shows
          them is mounted once, above all of them - see drill.jsx. */}
      <DrillProvider>
        <Dashboard {...props} />
      </DrillProvider>
    </PeriodProvider>
  );
}

function Dashboard({ openImport = false, onImportOpened }) {
  const [data, setData] = useState(null);
  const [tab, setTab] = useState('overview');
  // The member last used in each group. Bouncing between Ledger and Spending
  // should not make you re-navigate every time.
  const [lastInGroup, setLastInGroup] = useState({});
  const [menuOpen, setMenuOpen] = useState(false);
  const [showProfile, setShowProfile] = useState(false);
  const [mailboxOpen, setMailboxOpen] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [reviewCount, setReviewCount] = useState(0);
  /* Held in theme.js, not here. This component only renders once you are
     signed in and set up, so a theme owned by it left the sign-in screen and
     the setup wizard with no theme at all - which is to say, light, under a
     dark app. */
  const [theme, toggleTheme] = useTheme();

  const { params: periodParams, scoped, reportWindow } = usePeriod();
  /* Whether the operator's view is on offer, and whether the app is currently
     pointed at generated data rather than a real ledger. */
  const { isAdmin, user } = useAuth();
  /* The figures for the selected window, recomputed server-side. Held apart
     from `data` because the whole-ledger payload carries things a period
     cannot re-derive - the narrative and the transfer report - so a window
     replaces the arithmetic and keeps the rest. */
  const [windowed, setWindowed] = useState(null);
  const [windowError, setWindowError] = useState(null);
  const [windowing, setWindowing] = useState(false);

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

  /* One request per window, for the whole app. Overview, Spending and the
     Months tab all read this same answer, so switching between them does not
     re-ask the same question three times - and cannot get three answers. */
  const periodKey = JSON.stringify(periodParams);
  useEffect(() => {
    if (!scoped) { setWindowed(null); setWindowError(null); return undefined; }
    let cancelled = false;
    setWindowing(true);
    api.analysis(periodParams)
      .then((body) => {
        if (cancelled) return;
        setWindowed(body.status === 'ok' ? body : null);
        setWindowError(null);
        // The window the server actually applied, so the control describes
        // that rather than what it guessed it had asked for.
        reportWindow(periodKey, body.range);
      })
      .catch((e) => !cancelled && setWindowError(e.message))
      .finally(() => !cancelled && setWindowing(false));
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [periodKey, scoped, data]);

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

  // Read once for the header badge; the panel re-reads when it opens.
  const setup = useSetupStatus();
  /* Stable identities. A popover that binds document listeners keyed on its
     callbacks re-binds them on every render otherwise, and anything it does
     on mount runs again with them. */
  const toggleSetup = useCallback(() => setSetupOpen((v) => !v), []);
  const closeSetup = useCallback(() => setSetupOpen(false), []);
  const openProfile = useCallback(() => setShowProfile(true), []);
  const openImportFlow = useCallback(() => setMailboxOpen(true), []);

  const hasData = Boolean(data?.analysis?.totals);

  /* What the panels actually read.
   *
   * The window replaces `analysis` and nothing else. `accounts`, `loans` and
   * `forecast` are not period-scoped facts - a balance is as-of, an
   * amortization runs forward - and the narrative describes the whole ledger,
   * so it is passed through untouched and labelled where it is shown, rather
   * than being silently re-titled as if a model had written about this
   * window.
   */
  const viewData = useMemo(() => {
    if (!data) return data;
    if (!scoped || !windowed?.analysis) return { ...data, range: null };
    return {
      ...data,
      analysis: windowed.analysis,
      range: windowed.range,
      available: windowed.available,
    };
  }, [data, scoped, windowed]);

  // A window with nothing in it, as opposed to a ledger with nothing in it.
  const windowEmpty = scoped && windowed
    && !windowed.analysis?.totals?.transaction_count;

  // A group with nothing available in it is not shown at all: before an
  // import, Money and Transactions have no members, and an empty group is a
  // dead end wearing the same clothes as a live one.
  const visibleGroups = GROUPS
    .map(([group, label, members]) => [
      group, label,
      members.filter(([key]) => (hasData || ALWAYS_AVAILABLE.includes(key))
        && (!ADMIN_ONLY.includes(key) || isAdmin)),
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


        <div className="setup-slot">
          <SetupButton status={setup} open={setupOpen} onToggle={toggleSetup} />
          {setupOpen && (
            <SetupPanel
              status={setup}
              onClose={closeSetup}
              onNavigate={setTab}
              onProfile={openProfile}
              onImport={openImportFlow}
            />
          )}
        </div>

        <MailboxButton mailbox={mailbox} onOpen={() => setMailboxOpen(true)} />
        <AccountMenu onProfile={() => setShowProfile(true)} theme={theme} onToggleTheme={toggleTheme} />

      </header>

      <main className="main">
        {user?.demo_mode && (
          <DemoBanner />
        )}
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

            {/* One period, above whichever panel is open, so switching tabs
                keeps the window rather than resetting it. Only on the tabs it
                means something for - see PERIOD_TABS. */}
            {hasData && PERIOD_TABS.includes(tab) && <PeriodPicker />}
            {windowError && (
              <Callout tone="warn">
                The figures for this period could not be computed: {windowError}
              </Callout>
            )}
            {/* Kept out of the way of the numbers: a spinner in place of the
                figures would unmount the panel on every period change. */}
            {windowing && (
              <div className="period-loading">
                <span className="spinner" /> Recomputing for this period…
              </div>
            )}

            {/* A window with no rows in it must not render as a dashboard of
                zeros: "you earned nothing and spent nothing" is a claim, and
                the true one is "there is nothing here to report". */}
            {windowEmpty && ['overview', 'spending'].includes(tab) ? (
              <PeriodEmpty available={windowed?.available} />
            ) : (
              <>
                {tab === 'overview' && <Overview data={viewData} />}
                {tab === 'spending' && <Spending data={viewData} />}
              </>
            )}
            {tab === 'budget' && <Budget />}
            {tab === 'month-view' && <MonthView data={viewData} />}
            {tab === 'recurring' && <Recurring />}
            {tab === 'review' && <ReviewHub />}
            {tab === 'ledger' && <Ledger data={viewData} />}
            {tab === 'debt' && <Debt data={data} />}
            {tab === 'credit' && <CreditReport accounts={data?.accounts || []}
              onImport={() => setMailboxOpen(true)} />}
            {tab === 'portfolio' && <Portfolio onImport={() => setMailboxOpen(true)} />}
            {tab === 'explore' && <Explore />}
            {tab === 'agents' && <Agents />}
            {tab === 'position' && <Position />}
            {tab === 'data' && <DataHub data={data}
              onImport={() => setMailboxOpen(true)} />}
            {tab === 'rules' && <Rules />}
            {tab === 'settings' && <Settings onLedgerChanged={load} />}
            {tab === 'admin' && isAdmin && <Admin />}
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
