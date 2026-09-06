/* The application shell.
 *
 * Everything that is true on every screen lives here and nowhere else: the
 * rail, the bar, the period control, the import wizard, the drill-down sheet,
 * the command palette and the keyboard map. A screen is a component that
 * renders into `<main>` and nothing more, which is why every one of them fits
 * on a page or two.
 */

import React, {
  Suspense, useCallback, useEffect, useMemo, useState,
} from 'react';
import { useRoute } from '../core/router';
import { useAuth } from '../core/auth';
import { PeriodProvider } from '../core/period';
import { DrillProvider } from '../core/drill';
import { useDashboard, useReviewCount } from '../core/ledger';
import { readGlobal, writeGlobal } from '../core/storage';
import { setTheme, readTheme } from '../core/theme';
import { switchDemo } from '../core/api';
import Rail from './Rail';
import Topbar from './Topbar';
import CommandPalette from './CommandPalette';
import PeriodBar from './PeriodBar';
import { LAZY, routeFor } from './routes';
import ImportWizard from '../screens/import/ImportWizard';
import useImport from '../screens/import/useImport';
import {
  Button, Callout, Icon, Skeleton, SkeletonStats,
} from '../ui';

const RAIL_KEY = 'prism-rail-collapsed';

export default function App({ openImport, onImportOpened }) {
  return (
    <PeriodProvider>
      {/* Any screen can open the rows behind a figure, so the sheet that shows
          them is mounted once, above all of them - see core/drill.jsx. */}
      <DrillProvider>
        <Chrome openImport={openImport} onImportOpened={onImportOpened} />
      </DrillProvider>
    </PeriodProvider>
  );
}

function Chrome({ openImport, onImportOpened }) {
  const { path, navigate } = useRoute();
  const { user, isAdmin } = useAuth();
  const { hasLedger, stale, loading, refetch } = useDashboard();
  const reviewCount = useReviewCount();

  const [collapsed, setCollapsed] = useState(() => readGlobal(RAIL_KEY, false));
  const [drawer, setDrawer] = useState(false);
  const [palette, setPalette] = useState(false);
  const [wizard, setWizard] = useState(false);

  // One poller for the whole app. Held here rather than inside the wizard
  // because the bar's own badge needs the same answer, and because an import
  // that finishes while the wizard is closed still has to refresh the ledger.
  const mailbox = useImport({ open: wizard, onImported: refetch });

  const toggleRail = useCallback(() => {
    setCollapsed((v) => { writeGlobal(RAIL_KEY, !v); return !v; });
  }, []);

  const openWizard = useCallback(() => setWizard(true), []);

  /* The wizard's "Import statements" button finishes onboarding and asks the
     app to open the import flow, rather than leaving somebody who just said
     "yes, bring my statements in" looking at an empty dashboard. */
  useEffect(() => {
    if (openImport) { setWizard(true); onImportOpened?.(); }
  }, [openImport, onImportOpened]);

  /* ---- keyboard ----
     Deliberately few, and none of them a single letter that could fire while
     somebody is typing into a filter box. */
  useEffect(() => {
    const onKey = (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)
        || e.target.isContentEditable;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPalette((v) => !v);
        return;
      }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === '/') { e.preventDefault(); setPalette(true); }
      if (e.shiftKey && e.key === 'D') { setTheme(readTheme() === 'dark' ? 'light' : 'dark'); }
      if (e.shiftKey && e.key === 'I') { e.preventDefault(); setWizard(true); }
      if (e.key === 'Escape') setDrawer(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const route = routeFor(path);
  const known = route && (!route.adminOnly || isAdmin);
  const Screen = known ? LAZY[route.key] : null;

  useEffect(() => {
    document.title = route ? `${route.title} · Prism` : 'Prism';
  }, [route]);

  /* A screen that needs a ledger, on a workspace that has none, is not an
     error - it is somebody who has not imported anything yet, and what they
     need is the way in. */
  const blocked = known && route.needsLedger && !hasLedger && !loading;

  const content = useMemo(() => {
    if (!known) return <NotFound onHome={() => navigate('/')} />;
    if (loading && route.needsLedger) return <FirstLoad />;
    if (blocked) return <NoLedger onImport={openWizard} />;
    return (
      <Suspense fallback={<FirstLoad />}>
        <Screen onImport={openWizard} />
      </Suspense>
    );
  }, [known, loading, blocked, route, Screen, openWizard, navigate]);

  return (
    <div className={`shell ${collapsed ? 'rail-collapsed' : ''}`}>
      <Rail
        collapsed={collapsed}
        onToggle={toggleRail}
        open={drawer}
        onClose={() => setDrawer(false)}
        hasLedger={hasLedger}
        reviewCount={reviewCount}
      />
      {drawer && <div className="rail-scrim" onClick={() => setDrawer(false)} />}

      <main className="main">
        <Topbar
          title={route?.label || 'Not found'}
          onMenu={() => setDrawer(true)}
          onCommand={() => setPalette(true)}
          onImport={openWizard}
          importBadge={mailbox.activeCount}
        />

        <div className="page">
          {user?.demo_mode && <DemoBanner />}
          {stale && <Callout tone="warn">{stale}</Callout>}
          {/* One period, above whichever screen is open, so moving between
              them keeps the window rather than resetting it. Only on the
              screens it means something for - a loan's amortization runs from
              today to payoff, and a holdings statement is a photograph of one
              moment. */}
          {known && route.period && hasLedger && <PeriodBar />}
          {content}
        </div>
      </main>

      <CommandPalette
        open={palette}
        onClose={() => setPalette(false)}
        onImport={openWizard}
        hasLedger={hasLedger}
      />

      <ImportWizard
        mailbox={mailbox}
        open={wizard}
        onClose={() => setWizard(false)}
        onImported={refetch}
      />
    </div>
  );
}

/* ── states the shell owns ───────────────────────────────────────────────── */

function FirstLoad() {
  return (
    <>
      <SkeletonStats />
      <div className="grid cols-2">
        <div className="card"><div className="card-body"><Skeleton lines={7} /></div></div>
        <div className="card"><div className="card-body"><Skeleton lines={7} /></div></div>
      </div>
    </>
  );
}

function NoLedger({ onImport }) {
  return (
    <div style={{ maxWidth: 620, margin: '6vh auto 0', textAlign: 'center' }}>
      <h1 className="h1" style={{ fontSize: 26, marginBottom: 10 }}>
        Understand where your money actually goes
      </h1>
      <p className="lead" style={{ margin: '0 auto 20px' }}>
        Read your bank, card, loan and investment statements — from your mailbox or
        from files you have. Every figure is reconciled against the balances your bank
        printed, and nothing counts until you have seen what was read.
      </p>
      <Button variant="primary" size="lg" icon="upload" onClick={onImport}>
        Import statements
      </Button>
      <p className="small dim" style={{ marginTop: 16 }}>
        Scan Gmail or add files from this computer — both start in the same place.
        Password-protected PDFs open automatically once your details are filled in.
      </p>
    </div>
  );
}

function NotFound({ onHome }) {
  return (
    <div className="empty" style={{ marginTop: '6vh' }}>
      <span className="empty-mark"><Icon name="compass" size={19} /></span>
      <h3>There is nothing at this address</h3>
      <p>The link may be from an older version of the app, or mistyped.</p>
      <Button variant="primary" onClick={onHome} style={{ marginTop: 8 }}>
        Go to the overview
      </Button>
    </div>
  );
}

/* "You are looking at generated data."
 *
 * Loud, and above everything. The risk this exists to remove is not confusing
 * a demo for real data during a demo. It is coming back on Monday, forgetting
 * the switch is on, and concluding something about your own money from
 * somebody else's numbers. */
function DemoBanner() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  return (
    <div className="demo-banner" role="status">
      <span className="demo-badge">Demo</span>
      <span className="demo-text">
        Every figure on screen is <strong>generated</strong> — this is a demo workspace,
        not your ledger. Nothing you do here touches your own data.
        {error && <span className="neg"> {error}</span>}
      </span>
      <Button
        busy={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await switchDemo(false); // reloads on success
          } catch (e) { setError(e.message); setBusy(false); }
        }}
      >
        Show my real data
      </Button>
    </div>
  );
}

