/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Application Root Shell
   ──────────────────────────────────────────────────────────────────────── */

import React, {
  Suspense, useCallback, useEffect, useMemo, useState,
} from 'react';
import { useRoute } from '../core/router';
import { useAuth } from '../core/auth';
import { PeriodProvider } from '../core/period';
import { DrillProvider } from '../core/drill';
import { CopilotProvider, useCopilot } from '../core/copilot';
import { useDashboard, useReviewCount } from '../core/ledger';
import { readGlobal, writeGlobal } from '../core/storage';
import { setTheme, readTheme } from '../core/theme';
import { switchDemo } from '../core/api';
import Rail from './Rail';
import Topbar from './Topbar';
import CommandPalette from './CommandPalette';
import CopilotDrawer from './CopilotDrawer';
import PeriodBar from './PeriodBar';
import { LAZY, routeFor } from './routes';
import ErrorBoundary from './ErrorBoundary';
import ImportWizard from '../screens/import/ImportWizard';
import useImport from '../screens/import/useImport';
import { Button, Callout, Icon, Skeleton, SkeletonStats } from '../ui';

const RAIL_KEY = 'prism-v3-rail-collapsed';

export default function App({ openImport, onImportOpened }) {
  return (
    <PeriodProvider>
      <DrillProvider>
        <CopilotProvider>
          <Chrome openImport={openImport} onImportOpened={onImportOpened} />
        </CopilotProvider>
      </DrillProvider>
    </PeriodProvider>
  );
}

function Chrome({ openImport, onImportOpened }) {
  const { path, navigate } = useRoute();
  const { user, isAdmin } = useAuth();
  const { hasLedger, stale, loading, refetch } = useDashboard();
  const reviewCount = useReviewCount();
  const { toggleCopilot, setActiveScreen } = useCopilot();

  const [collapsed, setCollapsed] = useState(() => readGlobal(RAIL_KEY, false));
  const [drawer, setDrawer] = useState(false);
  const [palette, setPalette] = useState(false);
  const [wizard, setWizard] = useState(false);

  const mailbox = useImport({ open: wizard, onImported: refetch });

  const toggleRail = useCallback(() => {
    setCollapsed((v) => { writeGlobal(RAIL_KEY, !v); return !v; });
  }, []);

  const openWizard = useCallback(() => setWizard(true), []);

  useEffect(() => {
    if (openImport) { setWizard(true); onImportOpened?.(); }
  }, [openImport, onImportOpened]);

  /* Global Keyboard Shortcuts */
  useEffect(() => {
    const onKey = (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)
        || e.target.isContentEditable;

      // ⌘K or Ctrl+K: Command Palette
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPalette((v) => !v);
        return;
      }
      // ⌘J or Ctrl+J: AI Copilot
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'j') {
        e.preventDefault();
        toggleCopilot();
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
  }, [toggleCopilot]);

  const route = routeFor(path);
  const known = route && (!route.adminOnly || isAdmin);
  const Screen = known ? LAZY[route.key] : null;

  useEffect(() => {
    document.title = route ? `${route.title} · Prism v3` : 'Prism v3';
    if (route?.key) setActiveScreen(route.key);
  }, [route, setActiveScreen]);

  const blocked = known && route.needsLedger && !hasLedger && !loading;

  const content = useMemo(() => {
    if (!known) return <NotFound onHome={() => navigate('/')} />;
    if (loading && route.needsLedger) return <FirstLoad />;
    if (blocked) return <NoLedger onImport={openWizard} />;
    return (
      <ErrorBoundary resetKey={path}>
        <Suspense fallback={<FirstLoad />}>
          <Screen onImport={openWizard} />
        </Suspense>
      </ErrorBoundary>
    );
  }, [known, loading, blocked, route, Screen, openWizard, navigate, path]);

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
      {drawer && <div className="drawer-backdrop" onClick={() => setDrawer(false)} />}

      <main className="main">
        <Topbar
          title={route?.label || 'Not found'}
          onMenu={() => setDrawer(true)}
          onCommand={() => setPalette(true)}
          onImport={openWizard}
          importBadge={mailbox.activeCount}
        />

        <div className="page-container">
          {user?.demo_mode && <DemoBanner />}
          {stale && <Callout tone="warn">{stale}</Callout>}
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

      <CopilotDrawer />

      <ImportWizard
        mailbox={mailbox}
        open={wizard}
        onClose={() => setWizard(false)}
        onImported={refetch}
      />
    </div>
  );
}

function FirstLoad() {
  return (
    <div className="flex-col gap-4">
      <SkeletonStats />
      <div className="grid-2">
        <div className="card"><div className="card-body"><Skeleton lines={8} /></div></div>
        <div className="card"><div className="card-body"><Skeleton lines={8} /></div></div>
      </div>
    </div>
  );
}

function NoLedger({ onImport }) {
  return (
    <div style={{ maxWidth: 640, margin: '8vh auto 0', textAlign: 'center' }}>
      <div style={{
        width: 54, height: 54, borderRadius: 'var(--r-xl)',
        background: 'linear-gradient(135deg, var(--accent) 0%, #38bdf8 100%)',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        color: '#ffffff', marginBottom: 16, boxShadow: 'var(--shadow-glow)',
      }}>
        <Icon name="sparkles" size={26} />
      </div>
      <h1 style={{ fontSize: 28, fontWeight: 800, marginBottom: 10 }}>
        Welcome to Prism v3
      </h1>
      <p style={{ color: 'var(--text-2)', fontSize: 15, margin: '0 auto 24px', lineHeight: 1.6 }}>
        Upload your bank, credit card, loan and investment statements.
        Every figure ties out to the exact rupee with complete auditability,
        transfer netting, and proactive AI financial intelligence.
      </p>
      <Button variant="primary" size="lg" icon="upload" onClick={onImport}>
        Import Statements
      </Button>
      <p style={{ fontSize: 12.5, color: 'var(--text-3)', marginTop: 16 }}>
        Supports PDF, XLSX, CSV, and automatic Gmail statement sync.
      </p>
    </div>
  );
}

function NotFound({ onHome }) {
  return (
    <div style={{ textAlign: 'center', margin: '8vh auto 0' }}>
      <div style={{
        width: 48, height: 48, borderRadius: 'var(--r-lg)',
        background: 'var(--surface-2)', border: '1px solid var(--line)',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        marginBottom: 14, color: 'var(--text-3)',
      }}>
        <Icon name="compass" size={24} />
      </div>
      <h3 style={{ fontSize: 18, fontWeight: 700, marginBottom: 6 }}>Page Not Found</h3>
      <p style={{ color: 'var(--text-2)', marginBottom: 16 }}>This link may be outdated or mistyped.</p>
      <Button variant="primary" onClick={onHome}>Go to Overview</Button>
    </div>
  );
}

function DemoBanner() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  return (
    <div className="demo-banner">
      <div className="flex items-center gap-2">
        <span className="badge badge-accent" style={{ background: '#ffffff', color: '#d97706', fontWeight: 800 }}>
          DEMO MODE
        </span>
        <span>
          Showing <strong>synthetic fixture ledger</strong>. None of these transactions touch your actual data.
          {error && <span className="text-neg"> {error}</span>}
        </span>
      </div>
      <Button
        size="sm"
        busy={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await switchDemo(false);
          } catch (e) { setError(e.message); setBusy(false); }
        }}
      >
        Exit Demo
      </Button>
    </div>
  );
}
